import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildEmailSettingsUpdate,
  parseExportDirectoryLines,
  sanitizeCredentialKeys,
} from '../src/lib/connections.ts';

test('blank SMTP password does not overwrite the configured secret', () => {
  const update = buildEmailSettingsUpdate({
    host: 'smtp.example.com',
    port: 587,
    username: 'automation',
    password: '',
    from_email: 'rpa@example.com',
    start_tls: true,
    default_recipient: 'owner@example.com',
    credential_key: 'smtp_default',
  });

  assert.equal('password' in update, false);
  assert.equal(update.host, 'smtp.example.com');
  assert.equal(update.credential_key, 'smtp_default');
});

test('entered SMTP password is sent once and never echoed by helpers', () => {
  const update = buildEmailSettingsUpdate({
    host: 'smtp.example.com',
    port: 465,
    username: 'automation',
    password: 'new-secret',
    from_email: 'rpa@example.com',
    start_tls: false,
    default_recipient: '',
    credential_key: 'smtp_default',
  });

  assert.equal(update.password, 'new-secret');
  assert.equal(JSON.stringify(sanitizeCredentialKeys(['smtp_default'])), '["smtp_default"]');
});

test('export directories accept separators but reject non-absolute values', () => {
  assert.deepEqual(
    parseExportDirectoryLines('C:/Exports; D:/RPA\nC:/Exports'),
    ['C:/Exports', 'D:/RPA'],
  );
  assert.deepEqual(parseExportDirectoryLines('relative/path'), []);
});

test('credential keys are sorted and contain no values', () => {
  assert.deepEqual(
    sanitizeCredentialKeys(['zeta', 'alpha', 'zeta']),
    ['alpha', 'zeta'],
  );
});
