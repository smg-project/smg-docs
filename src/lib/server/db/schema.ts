import { integer, sqliteTable, text } from 'drizzle-orm/sqlite-core';

export const contentBlocks = sqliteTable('content_blocks', {
	key: text('key').primaryKey(),
	value: text('value').notNull(),
	updatedAt: text('updated_at')
		.notNull()
		.$defaultFn(() => new Date().toISOString())
});

export const mediaAssets = sqliteTable('media_assets', {
	key: text('key').primaryKey(),
	r2Key: text('r2_key').notNull(),
	contentType: text('content_type'),
	updatedAt: text('updated_at')
		.notNull()
		.$defaultFn(() => new Date().toISOString())
});

export const adminUsers = sqliteTable('admin_users', {
	id: integer('id').primaryKey({ autoIncrement: true }),
	email: text('email').notNull().unique(),
	passwordHash: text('password_hash').notNull()
});

export const sessions = sqliteTable('sessions', {
	id: text('id').primaryKey(),
	userId: integer('user_id')
		.notNull()
		.references(() => adminUsers.id),
	expiresAt: text('expires_at').notNull()
});
