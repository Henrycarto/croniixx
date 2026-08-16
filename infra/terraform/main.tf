terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State lives in S3 with DynamoDB locking. Two engineers applying at once
  # against local state would produce a cluster that matches neither plan.
  backend "s3" {
    bucket         = "croniixx-terraform-state"
    key            = "infra/terraform.tfstate"
    region         = "eu-central-1"
    dynamodb_table = "croniixx-terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "croniixx"
      Environment = var.environment
      ManagedBy   = "terraform"
      # Everything in this account touches protected health information.
      # Tagging it makes that visible in cost and access reports rather than
      # something people have to remember.
      DataClass = "phi"
    }
  }
}

variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Environment must be staging or production."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "domain_name" {
  type        = string
  description = "Public hostname for the clinician dashboard"
}

locals {
  name = "croniixx-${var.environment}"

  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  services = {
    sync = {
      port          = 8001
      cpu           = 512
      memory        = 1024
      desired_count = 2
      health_path   = "/health"
    }
    engine = {
      port          = 8002
      cpu           = 1024
      memory        = 2048
      desired_count = 2
      health_path   = "/health"
    }
    reminder-api = {
      port = 8003
      cpu  = 512
      # The dispatcher runs inside the service process and holds claims for a
      # visibility window, so this one is deliberately not scaled wide. Extra
      # tasks add contention on the same queue rather than throughput.
      memory        = 1024
      desired_count = 2
      health_path   = "/health"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.name}-public-${count.index}" }
}

# Services and the database sit in private subnets. Nothing holding patient
# data is reachable from the internet without passing the load balancer.
resource "aws_subnet" "private" {
  count = length(local.azs)

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = local.azs[count.index]

  tags = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_eip" "nat" {
  count  = length(local.azs)
  domain = "vpc"
  tags   = { Name = "${local.name}-nat-${count.index}" }
}

# One NAT gateway per zone. A single shared gateway is cheaper and makes a
# zone failure take down outbound Terra polling for the whole cluster.
resource "aws_nat_gateway" "main" {
  count = length(local.azs)

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  depends_on = [aws_internet_gateway.main]
  tags       = { Name = "${local.name}-nat-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table" "private" {
  count  = length(local.azs)
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_route_table_association" "public" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public entry point"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "service" {
  name        = "${local.name}-service"
  description = "Fargate tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Load balancer to service ports"
    from_port       = 8001
    to_port         = 8003
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description = "Service to service inside the VPC"
    from_port   = 8001
    to_port     = 8003
    protocol    = "tcp"
    self        = true
  }

  egress {
    description = "Outbound to Terra, Expo, and AWS APIs"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "data" {
  name        = "${local.name}-data"
  description = "Postgres and Redis"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Postgres from services only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.service.id]
  }

  ingress {
    description     = "Redis from services only"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.service.id]
  }
}

# ---------------------------------------------------------------------------
# Load balancer
# ---------------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = local.name
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  drop_invalid_header_fields = true
  enable_deletion_protection = var.environment == "production"
}

resource "aws_lb_target_group" "service" {
  for_each = local.services

  name        = substr("${local.name}-${each.key}", 0, 32)
  port        = each.value.port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  health_check {
    path                = each.value.health_path
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # Long enough for an in flight Terra webhook to finish being written before
  # the task goes away, short enough that a deploy does not stall on it.
  deregistration_delay = 30
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}
