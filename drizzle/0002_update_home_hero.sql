UPDATE `content_blocks`
SET
  `value` = 'The High-performance inference gateway for production LLM deployments',
  `updated_at` = datetime('now')
WHERE `key` = 'home.hero.title';

UPDATE `content_blocks`
SET
  `value` = 'Route, balance, and orchestrate traffic across your LLM fleet with enterprise-grade reliability.',
  `updated_at` = datetime('now')
WHERE `key` = 'home.hero.subtitle';
