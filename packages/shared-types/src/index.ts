/**
 * Types shared by the web dashboard, the mobile app, and anything else that
 * consumes a Croniixx service. These mirror the Pydantic models in
 * services/engine/app/schemas.py and services/sync/app/schemas.py.
 *
 * Kept hand written rather than generated. The generated output would be an
 * exact structural copy including every optional field the API happens to
 * emit, and the point of this file is to state the contract the clients may
 * rely on, which is narrower.
 */

export * from './envelope'
export * from './circadian'
export * from './schedule'
export * from './wearables'
export * from './dial'
