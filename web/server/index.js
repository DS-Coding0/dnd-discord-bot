const express = require('express');
const mysql = require('mysql2/promise');
const dotenv = require('dotenv');
const cors = require('cors');
const axios = require('axios');
dotenv.config({ path: '../.env' });  // Lädt Root-.env!

const app = express();
app.use(cors());
app.use(express.json());

const pool = mysql.createPool({
  host: process.env.DB_HOST || 'localhost',
  user: process.env.DB_USER || 'root',
  password: process.env.DB_PASS,
  database: process.env.DB_NAME || 'dndbot',
  port: process.env.DB_PORT || 3306
});

const DND_API = process.env.DND_API_BASE || 'https://www.dnd5eapi.co/api';

app.get('/', (req, res) => res.send('DnD Webapp läuft!'));

app.get('/api/classes/:name', async (req, res) => {
  try {
    const { data } = await axios.get(`${DND_API}/classes/${req.params.name}`);
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/characters/:discordId', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      'SELECT * FROM characters c JOIN users u ON c.user_id = u.id WHERE u.discord_id = ?',
      [req.params.discordId]
    );
    res.json(rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Webapp läuft auf Port ${PORT}`));
