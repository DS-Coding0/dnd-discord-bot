export const redisConfig = {
  host: process.env.REDIS_HOST || 'disabled',
  port: process.env.REDIS_PORT || 0,
  enabled: process.env.REDIS_ENABLED === 'true'
};
