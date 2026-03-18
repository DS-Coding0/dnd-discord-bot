// prisma.config.js  ← .JS statt .TS!
const { defineConfig } = require('prisma/config');
require('dotenv').config({ path: require('path').join(__dirname, '..', '.env') });

module.exports = defineConfig({
  schema: './prisma/schema.prisma',
  datasource: {
    url: process.env.DATABASE_URL ?? 'mysql://root:dein_passwort@localhost:3306/dndbot',
  },
});
