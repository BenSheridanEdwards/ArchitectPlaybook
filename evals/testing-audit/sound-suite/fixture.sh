#!/usr/bin/env bash
# Builds the same TypeScript invoicing service and React admin screen with a suite that would catch real bugs.
set -euo pipefail
export GIT_AUTHOR_NAME=Fixture GIT_AUTHOR_EMAIL=fixture@example.com GIT_COMMITTER_NAME=Fixture GIT_COMMITTER_EMAIL=fixture@example.com
git init -q
w() { mkdir -p "$(dirname "$1")"; cat > "$1"; }
commit() { local days="$1"; shift; local when; when="$(date -u -v-"${days}"d +%Y-%m-%dT12:00:00Z 2>/dev/null || date -u -d "-${days} days" +%Y-%m-%dT12:00:00Z)"; git add -A; GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" git commit -qm "$*"; }

w package.json <<'EOF'
{
  "name": "invoicing",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/server/main.ts",
    "build": "tsc --noEmit && vite build",
    "typecheck": "tsc --noEmit",
    "lint": "eslint .",
    "test": "vitest run --coverage"
  },
  "dependencies": {
    "fastify": "5.4.0",
    "pg": "8.16.3",
    "react": "19.1.1",
    "react-dom": "19.1.1",
    "zod": "3.25.76"
  },
  "devDependencies": {
    "@eslint/js": "9.33.0",
    "@testcontainers/postgresql": "11.5.1",
    "@testing-library/jest-dom": "6.6.4",
    "@testing-library/react": "16.3.0",
    "@testing-library/user-event": "14.6.1",
    "@types/pg": "8.15.5",
    "@types/react": "19.1.10",
    "@types/react-dom": "19.1.7",
    "@vitejs/plugin-react": "5.0.0",
    "@vitest/coverage-v8": "3.2.4",
    "eslint": "9.33.0",
    "eslint-plugin-jest-dom": "5.5.0",
    "eslint-plugin-testing-library": "7.6.6",
    "jsdom": "26.1.0",
    "msw": "2.10.5",
    "tsx": "4.20.4",
    "typescript": "5.9.2",
    "typescript-eslint": "8.39.1",
    "vite": "7.1.2",
    "vitest": "3.2.4"
  }
}
EOF
w tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true,
    "noEmit": true
  },
  "include": ["src"]
}
EOF
w vite.config.ts <<'EOF'
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': { target: 'http://localhost:3000', rewrite: (path) => path.replace(/^\/api/, '') } } },
});
EOF
w vitest.config.ts <<'EOF'
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  test: {
    setupFiles: ['./src/test/setup.ts'],
    hookTimeout: 120_000,
    coverage: {
      provider: 'v8',
      include: ['src/**'],
      exclude: ['src/test/**', 'src/server/main.ts', 'src/ui/main.tsx'],
      thresholds: { lines: 85, functions: 85, statements: 85, branches: 80 },
    },
  },
});
EOF
w eslint.config.js <<'EOF'
import js from '@eslint/js';
import jestDom from 'eslint-plugin-jest-dom';
import testingLibrary from 'eslint-plugin-testing-library';
import tseslint from 'typescript-eslint';

const componentTests = ['src/ui/**/*.test.tsx'];

export default tseslint.config(
  { ignores: ['dist', 'coverage'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { files: componentTests, ...testingLibrary.configs['flat/react'] },
  { files: componentTests, ...jestDom.configs['flat/recommended'] },
  {
    files: componentTests,
    rules: {
      'testing-library/prefer-user-event': 'error',
      'testing-library/prefer-explicit-assert': 'error',
    },
  },
);
EOF
w .github/workflows/ci.yml <<'EOF'
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm }
      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck
      - run: npm test
EOF
w .gitignore <<'EOF'
node_modules
dist
coverage
EOF
w index.html <<'EOF'
<!doctype html>
<html lang="en">
  <head><meta charset="UTF-8" /><title>Invoices</title></head>
  <body><div id="root"></div><script type="module" src="/src/ui/main.tsx"></script></body>
</html>
EOF
w db/migrations/001_create_invoices.sql <<'EOF'
create table if not exists invoices (
  id text primary key,
  number text not null unique,
  customer_id text not null,
  status text not null check (status in ('draft', 'sent', 'paid', 'void')),
  total_pence integer not null check (total_pence >= 0),
  refunded_pence integer not null default 0,
  paid_at timestamptz,
  constraint refunds_within_total check (refunded_pence between 0 and total_pence)
);

create index if not exists invoices_customer_id on invoices (customer_id);
EOF
w src/billing/migrate.ts <<'EOF'
import { readdir, readFile } from 'node:fs/promises';
import type { Pool } from 'pg';

const MIGRATIONS = new URL('../../db/migrations/', import.meta.url);

export async function migrate(pool: Pool): Promise<void> {
  const names = (await readdir(MIGRATIONS)).filter((name) => name.endsWith('.sql')).sort();
  for (const name of names) {
    await pool.query(await readFile(new URL(name, MIGRATIONS), 'utf8'));
  }
}
EOF
w src/billing/money.ts <<'EOF'
export function formatPence(pence: number): string {
  const sign = pence < 0 ? '-' : '';
  return `${sign}£${(Math.abs(pence) / 100).toFixed(2)}`;
}
EOF
w src/billing/invoiceTotals.ts <<'EOF'
export type InvoiceLine = { description: string; unitPence: number; quantity: number; taxRate: number };

export type InvoiceTotals = { subtotal: number; discount: number; tax: number; total: number };

export function lineSubtotal(line: InvoiceLine): number {
  return line.unitPence * line.quantity;
}

export function invoiceTotals(lines: InvoiceLine[]): InvoiceTotals {
  const subtotal = lines.reduce((sum, line) => sum + lineSubtotal(line), 0);
  const tax = lines.reduce((sum, line) => sum + Math.round(lineSubtotal(line) * line.taxRate), 0);
  return { subtotal, discount: 0, tax, total: subtotal + tax };
}
EOF
w src/billing/invoiceRepository.ts <<'EOF'
import type { Pool } from 'pg';

export type InvoiceStatus = 'draft' | 'sent' | 'paid' | 'void';

export type Invoice = {
  id: string;
  number: string;
  customerId: string;
  status: InvoiceStatus;
  totalPence: number;
  refundedPence: number;
  paidAt: Date | null;
};

type InvoiceRow = {
  id: string;
  number: string;
  customer_id: string;
  status: InvoiceStatus;
  total_pence: number;
  refunded_pence: number;
  paid_at: Date | null;
};

const COLUMNS = 'id, number, customer_id, status, total_pence, refunded_pence, paid_at';

function toInvoice(row: InvoiceRow): Invoice {
  return {
    id: row.id,
    number: row.number,
    customerId: row.customer_id,
    status: row.status,
    totalPence: row.total_pence,
    refundedPence: row.refunded_pence,
    paidAt: row.paid_at,
  };
}

export function invoiceRepository(pool: Pool) {
  return {
    async find(id: string): Promise<Invoice | null> {
      const result = await pool.query<InvoiceRow>(`select ${COLUMNS} from invoices where id = $1`, [id]);
      return result.rows[0] ? toInvoice(result.rows[0]) : null;
    },
    async listForCustomer(customerId: string): Promise<Invoice[]> {
      const result = await pool.query<InvoiceRow>(
        `select ${COLUMNS} from invoices where customer_id = $1 order by number desc`,
        [customerId],
      );
      return result.rows.map(toInvoice);
    },
    async recordRefund(id: string, amountPence: number): Promise<void> {
      await pool.query('update invoices set refunded_pence = refunded_pence + $2 where id = $1', [id, amountPence]);
    },
  };
}

export type InvoiceRepository = ReturnType<typeof invoiceRepository>;
EOF
w src/notifications/mailer.ts <<'EOF'
import { formatPence } from '../billing/money';

export type EmailMessage = { to: string; subject: string; text: string };

export type Mailer = { send(message: EmailMessage): Promise<void> };

export function httpMailer(serverToken: string, endpoint = 'https://api.postmarkapp.com/email'): Mailer {
  return {
    async send(message) {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Postmark-Server-Token': serverToken },
        body: JSON.stringify({ From: 'billing@example.com', To: message.to, Subject: message.subject, TextBody: message.text }),
      });
      if (!response.ok) throw new Error(`Email provider returned ${response.status}`);
    },
  };
}

export function refundReceipt(email: string, invoiceId: string, amountPence: number): EmailMessage {
  return {
    to: email,
    subject: `Refund for invoice ${invoiceId}`,
    text: `We have refunded ${formatPence(amountPence)} to your original payment method.`,
  };
}
EOF
w src/server/app.ts <<'EOF'
import Fastify from 'fastify';
import type { InvoiceRepository } from '../billing/invoiceRepository';
import type { Mailer } from '../notifications/mailer';

export type AppDependencies = { invoices: InvoiceRepository; mailer: Mailer; now: () => Date };

export function buildApp(deps: AppDependencies) {
  const app = Fastify();

  app.get<{ Params: { id: string } }>('/invoices/:id', async (request, reply) => {
    const invoice = await deps.invoices.find(request.params.id);
    if (!invoice) return reply.code(404).send({ error: 'not-found' });
    return invoice;
  });

  app.get<{ Params: { id: string } }>('/customers/:id/invoices', async (request) =>
    deps.invoices.listForCustomer(request.params.id),
  );

  return app;
}
EOF
w src/server/main.ts <<'EOF'
import pg from 'pg';
import { invoiceRepository } from '../billing/invoiceRepository';
import { migrate } from '../billing/migrate';
import { httpMailer } from '../notifications/mailer';
import { buildApp } from './app';

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
await migrate(pool);
const app = buildApp({
  invoices: invoiceRepository(pool),
  mailer: httpMailer(process.env.POSTMARK_SERVER_TOKEN ?? ''),
  now: () => new Date(),
});

await app.listen({ port: Number(process.env.PORT ?? 3000), host: '0.0.0.0' });
EOF
w src/test/setup.ts <<'EOF'
import '@testing-library/jest-dom/vitest';
EOF
w src/test/postgres.ts <<'EOF'
import { PostgreSqlContainer } from '@testcontainers/postgresql';
import pg from 'pg';
import { migrate } from '../billing/migrate';

export type TestDatabase = { pool: pg.Pool; stop(): Promise<void> };

export async function startDatabase(): Promise<TestDatabase> {
  const container = await new PostgreSqlContainer('postgres:17-alpine').start();
  const pool = new pg.Pool({ connectionString: container.getConnectionUri() });
  await migrate(pool);
  return {
    pool,
    async stop() {
      await pool.end();
      await container.stop();
    },
  };
}

export type InvoiceSeed = {
  id: string;
  number: string;
  customerId: string;
  status: 'draft' | 'sent' | 'paid' | 'void';
  totalPence: number;
  refundedPence?: number;
  paidAt?: string | null;
};

export async function insertInvoice(pool: pg.Pool, invoice: InvoiceSeed): Promise<void> {
  await pool.query(
    'insert into invoices (id, number, customer_id, status, total_pence, refunded_pence, paid_at) values ($1, $2, $3, $4, $5, $6, $7)',
    [invoice.id, invoice.number, invoice.customerId, invoice.status, invoice.totalPence, invoice.refundedPence ?? 0, invoice.paidAt ?? null],
  );
}
EOF
w src/billing/invoiceTotals.test.ts <<'EOF'
import { describe, expect, it } from 'vitest';
import { invoiceTotals, type InvoiceLine } from './invoiceTotals';

const consulting: InvoiceLine = { description: 'Consulting', unitPence: 10_000, quantity: 3, taxRate: 0.2 };
const books: InvoiceLine = { description: 'Books', unitPence: 1_500, quantity: 2, taxRate: 0 };

describe('invoiceTotals', () => {
  it('taxes each line at its own rate', () => {
    expect(invoiceTotals([consulting, books])).toEqual({ subtotal: 33_000, discount: 0, tax: 6_000, total: 39_000 });
  });

  it('rounds tax to the nearest penny on each line', () => {
    const odd: InvoiceLine = { description: 'Postage', unitPence: 333, quantity: 1, taxRate: 0.2 };
    expect(invoiceTotals([odd, odd]).tax).toBe(134);
  });

  it('totals an empty invoice to zero', () => {
    expect(invoiceTotals([])).toEqual({ subtotal: 0, discount: 0, tax: 0, total: 0 });
  });
});
EOF
w src/billing/invoiceRepository.integration.test.ts <<'EOF'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { insertInvoice, startDatabase, type TestDatabase } from '../test/postgres';
import { invoiceRepository } from './invoiceRepository';

let database: TestDatabase;

beforeAll(async () => {
  database = await startDatabase();
});

afterAll(async () => {
  await database.stop();
});

beforeEach(async () => {
  await database.pool.query('truncate invoices');
});

describe('invoiceRepository', () => {
  it('reads an invoice with its payment time', async () => {
    await insertInvoice(database.pool, {
      id: 'inv_1', number: 'INV-001', customerId: 'cus_1', status: 'paid', totalPence: 10_000, paidAt: '2026-03-01T12:00:00Z',
    });
    expect(await invoiceRepository(database.pool).find('inv_1')).toEqual({
      id: 'inv_1',
      number: 'INV-001',
      customerId: 'cus_1',
      status: 'paid',
      totalPence: 10_000,
      refundedPence: 0,
      paidAt: new Date('2026-03-01T12:00:00Z'),
    });
  });

  it('returns null for an unknown invoice', async () => {
    expect(await invoiceRepository(database.pool).find('inv_missing')).toBeNull();
  });

  it('adds each refund to the amount already refunded', async () => {
    await insertInvoice(database.pool, { id: 'inv_1', number: 'INV-001', customerId: 'cus_1', status: 'paid', totalPence: 10_000 });
    const invoices = invoiceRepository(database.pool);
    await invoices.recordRefund('inv_1', 2_500);
    await invoices.recordRefund('inv_1', 1_000);
    expect((await invoices.find('inv_1'))?.refundedPence).toBe(3_500);
  });

  it('refuses a refund that would take the refunded amount past the total', async () => {
    await insertInvoice(database.pool, { id: 'inv_1', number: 'INV-001', customerId: 'cus_1', status: 'paid', totalPence: 1_000 });
    await expect(invoiceRepository(database.pool).recordRefund('inv_1', 1_001)).rejects.toThrow('refunds_within_total');
  });

  it("lists one customer's invoices, newest number first", async () => {
    await insertInvoice(database.pool, { id: 'inv_1', number: 'INV-001', customerId: 'cus_1', status: 'paid', totalPence: 1_000 });
    await insertInvoice(database.pool, { id: 'inv_2', number: 'INV-002', customerId: 'cus_1', status: 'sent', totalPence: 2_000 });
    await insertInvoice(database.pool, { id: 'inv_3', number: 'INV-003', customerId: 'cus_2', status: 'sent', totalPence: 3_000 });
    const invoices = await invoiceRepository(database.pool).listForCustomer('cus_1');
    expect(invoices.map((invoice) => invoice.number)).toEqual(['INV-002', 'INV-001']);
  });
});
EOF
w src/notifications/mailer.test.ts <<'EOF'
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { httpMailer, refundReceipt } from './mailer';

const ENDPOINT = 'https://api.postmarkapp.com/email';
const received: { token: string | null; body: unknown }[] = [];
const provider = setupServer(
  http.post(ENDPOINT, async ({ request }) => {
    received.push({ token: request.headers.get('X-Postmark-Server-Token'), body: await request.json() });
    return HttpResponse.json({ ErrorCode: 0 });
  }),
);

beforeAll(() => provider.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  provider.resetHandlers();
  received.length = 0;
});
afterAll(() => provider.close());

describe('httpMailer', () => {
  it('posts the message to the provider with the server token', async () => {
    await httpMailer('server-token').send({ to: 'ada@example.com', subject: 'Hello', text: 'Body' });
    expect(received).toEqual([
      {
        token: 'server-token',
        body: { From: 'billing@example.com', To: 'ada@example.com', Subject: 'Hello', TextBody: 'Body' },
      },
    ]);
  });

  it('fails when the provider rejects the message', async () => {
    provider.use(http.post(ENDPOINT, () => new HttpResponse(null, { status: 422 })));
    await expect(httpMailer('server-token').send({ to: 'ada@example.com', subject: 'Hello', text: 'Body' })).rejects.toThrow(
      'Email provider returned 422',
    );
  });
});

describe('refundReceipt', () => {
  it('tells the customer how much was refunded for which invoice', () => {
    expect(refundReceipt('ada@example.com', 'inv_7', 1_250)).toEqual({
      to: 'ada@example.com',
      subject: 'Refund for invoice inv_7',
      text: 'We have refunded £12.50 to your original payment method.',
    });
  });
});
EOF
w src/server/app.integration.test.ts <<'EOF'
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { invoiceRepository } from '../billing/invoiceRepository';
import { httpMailer } from '../notifications/mailer';
import { insertInvoice, startDatabase, type TestDatabase } from '../test/postgres';
import { buildApp } from './app';

const sentEmails: unknown[] = [];
const emailProvider = setupServer(
  http.post('https://api.postmarkapp.com/email', async ({ request }) => {
    sentEmails.push(await request.json());
    return HttpResponse.json({ ErrorCode: 0 });
  }),
);
let database: TestDatabase;

beforeAll(async () => {
  database = await startDatabase();
  emailProvider.listen({ onUnhandledRequest: 'error' });
});

afterAll(async () => {
  emailProvider.close();
  await database.stop();
});

beforeEach(async () => {
  sentEmails.length = 0;
  await database.pool.query('truncate invoices');
});

function appOn(day: string) {
  return buildApp({ invoices: invoiceRepository(database.pool), mailer: httpMailer('test-server-token'), now: () => new Date(day) });
}

async function refundedPence(id: string): Promise<number> {
  const result = await database.pool.query<{ refunded_pence: number }>('select refunded_pence from invoices where id = $1', [id]);
  return result.rows[0].refunded_pence;
}

describe('GET /invoices/:id', () => {
  it('returns the stored invoice', async () => {
    await insertInvoice(database.pool, {
      id: 'inv_1', number: 'INV-001', customerId: 'cus_1', status: 'paid', totalPence: 10_000, paidAt: '2026-03-01T12:00:00Z',
    });
    const response = await appOn('2026-03-10T12:00:00Z').inject({ method: 'GET', url: '/invoices/inv_1' });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      id: 'inv_1',
      number: 'INV-001',
      customerId: 'cus_1',
      status: 'paid',
      totalPence: 10_000,
      refundedPence: 0,
      paidAt: '2026-03-01T12:00:00.000Z',
    });
  });

  it('answers 404 for an unknown invoice', async () => {
    const response = await appOn('2026-03-10T12:00:00Z').inject({ method: 'GET', url: '/invoices/inv_missing' });
    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({ error: 'not-found' });
  });
});
EOF
commit 150 "feat: invoice totals, repository, and receipts"

w src/server/app.ts <<'EOF'
import Fastify from 'fastify';
import { z } from 'zod';
import type { InvoiceRepository } from '../billing/invoiceRepository';
import { decideRefund } from '../billing/refunds';
import type { Mailer } from '../notifications/mailer';
import { refundReceipt } from '../notifications/mailer';

export type AppDependencies = { invoices: InvoiceRepository; mailer: Mailer; now: () => Date };

const refundRequest = z.object({ amountPence: z.number().int(), email: z.string().email() });

export function buildApp(deps: AppDependencies) {
  const app = Fastify();

  app.get<{ Params: { id: string } }>('/invoices/:id', async (request, reply) => {
    const invoice = await deps.invoices.find(request.params.id);
    if (!invoice) return reply.code(404).send({ error: 'not-found' });
    return invoice;
  });

  app.get<{ Params: { id: string } }>('/customers/:id/invoices', async (request) =>
    deps.invoices.listForCustomer(request.params.id),
  );

  app.post<{ Params: { id: string } }>('/invoices/:id/refunds', async (request, reply) => {
    const body = refundRequest.safeParse(request.body);
    if (!body.success) return reply.code(400).send({ error: 'invalid-request' });
    const invoice = await deps.invoices.find(request.params.id);
    if (!invoice) return reply.code(404).send({ error: 'not-found' });
    const decision = decideRefund(invoice, body.data.amountPence, deps.now());
    if (!decision.ok) return reply.code(422).send({ error: decision.reason });
    await deps.invoices.recordRefund(invoice.id, decision.amountPence);
    await deps.mailer.send(refundReceipt(body.data.email, invoice.id, decision.amountPence));
    return reply.code(201).send({ refundedPence: decision.amountPence });
  });

  return app;
}
EOF
w src/billing/refunds.ts <<'EOF'
import type { Invoice } from './invoiceRepository';

export const REFUND_WINDOW_DAYS = 14;

export type RefundDecision = { ok: true; amountPence: number } | { ok: false; reason: 'window-closed' };

export function decideRefund(invoice: Invoice, requestedPence: number, now: Date): RefundDecision {
  const ageDays = (now.getTime() - (invoice.paidAt ?? now).getTime()) / 86_400_000;
  if (ageDays > REFUND_WINDOW_DAYS) return { ok: false, reason: 'window-closed' };
  return { ok: true, amountPence: requestedPence };
}
EOF
w src/billing/refunds.test.ts <<'EOF'
import { describe, expect, it } from 'vitest';
import type { Invoice } from './invoiceRepository';
import { decideRefund } from './refunds';

const now = new Date('2026-03-31T12:00:00Z');

function paidInvoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: 'inv_1',
    number: 'INV-001',
    customerId: 'cus_1',
    status: 'paid',
    totalPence: 10_000,
    refundedPence: 0,
    paidAt: new Date('2026-03-25T12:00:00Z'),
    ...overrides,
  };
}

describe('decideRefund', () => {
  it('refunds part of a paid invoice', () => {
    expect(decideRefund(paidInvoice(), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('still refunds on the fourteenth day after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-03-17T12:00:00Z') }), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('refuses a refund fifteen days after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-03-16T12:00:00Z') }), 2_500, now)).toEqual({ ok: false, reason: 'window-closed' });
  });
});
EOF
cat >> src/server/app.integration.test.ts <<'EOF'

describe('POST /invoices/:id/refunds', () => {
  it('records the refund and emails the customer a receipt', async () => {
    await insertInvoice(database.pool, {
      id: 'inv_1', number: 'INV-001', customerId: 'cus_1', status: 'paid', totalPence: 10_000, paidAt: '2026-03-01T12:00:00Z',
    });
    const response = await appOn('2026-03-10T12:00:00Z').inject({
      method: 'POST',
      url: '/invoices/inv_1/refunds',
      payload: { amountPence: 2_500, email: 'ada@example.com' },
    });
    expect(response.statusCode).toBe(201);
    expect(response.json()).toEqual({ refundedPence: 2_500 });
    expect(await refundedPence('inv_1')).toBe(2_500);
    expect(sentEmails).toEqual([
      {
        From: 'billing@example.com',
        To: 'ada@example.com',
        Subject: 'Refund for invoice inv_1',
        TextBody: 'We have refunded £25.00 to your original payment method.',
      },
    ]);
  });

  it('refuses a refund once the window has closed, and changes nothing', async () => {
    await insertInvoice(database.pool, {
      id: 'inv_1', number: 'INV-001', customerId: 'cus_1', status: 'paid', totalPence: 10_000, paidAt: '2026-02-01T12:00:00Z',
    });
    const response = await appOn('2026-03-10T12:00:00Z').inject({
      method: 'POST',
      url: '/invoices/inv_1/refunds',
      payload: { amountPence: 2_500, email: 'ada@example.com' },
    });
    expect(response.statusCode).toBe(422);
    expect(response.json()).toEqual({ error: 'window-closed' });
    expect(await refundedPence('inv_1')).toBe(0);
    expect(sentEmails).toEqual([]);
  });

  it('rejects a request without a valid email address', async () => {
    await insertInvoice(database.pool, {
      id: 'inv_1', number: 'INV-001', customerId: 'cus_1', status: 'paid', totalPence: 10_000, paidAt: '2026-03-01T12:00:00Z',
    });
    const response = await appOn('2026-03-10T12:00:00Z').inject({
      method: 'POST',
      url: '/invoices/inv_1/refunds',
      payload: { amountPence: 2_500, email: 'not-an-address' },
    });
    expect(response.statusCode).toBe(400);
    expect(await refundedPence('inv_1')).toBe(0);
  });
});
EOF
commit 120 "feat: refund paid invoices within 14 days"

w src/billing/refunds.ts <<'EOF'
import type { Invoice } from './invoiceRepository';

export const REFUND_WINDOW_DAYS = 30;

export type RefundDecision = { ok: true; amountPence: number } | { ok: false; reason: 'window-closed' };

export function decideRefund(invoice: Invoice, requestedPence: number, now: Date): RefundDecision {
  const ageDays = (now.getTime() - (invoice.paidAt ?? now).getTime()) / 86_400_000;
  if (ageDays > REFUND_WINDOW_DAYS) return { ok: false, reason: 'window-closed' };
  return { ok: true, amountPence: requestedPence };
}
EOF
w src/billing/refunds.test.ts <<'EOF'
import { describe, expect, it } from 'vitest';
import type { Invoice } from './invoiceRepository';
import { decideRefund } from './refunds';

const now = new Date('2026-03-31T12:00:00Z');

function paidInvoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: 'inv_1',
    number: 'INV-001',
    customerId: 'cus_1',
    status: 'paid',
    totalPence: 10_000,
    refundedPence: 0,
    paidAt: new Date('2026-03-25T12:00:00Z'),
    ...overrides,
  };
}

describe('decideRefund', () => {
  it('refunds part of a paid invoice', () => {
    expect(decideRefund(paidInvoice(), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('still refunds on the thirtieth day after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-03-01T12:00:00Z') }), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('refuses a refund thirty-one days after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-02-28T12:00:00Z') }), 2_500, now)).toEqual({ ok: false, reason: 'window-closed' });
  });
});
EOF
commit 95 "fix: allow refunds for 30 days, as the terms say"

w src/billing/refunds.ts <<'EOF'
import type { Invoice } from './invoiceRepository';

export const REFUND_WINDOW_DAYS = 30;

export type RefundDecision =
  | { ok: true; amountPence: number }
  | { ok: false; reason: 'window-closed' | 'exceeds-balance' };

export function decideRefund(invoice: Invoice, requestedPence: number, now: Date): RefundDecision {
  const ageDays = (now.getTime() - (invoice.paidAt ?? now).getTime()) / 86_400_000;
  if (ageDays > REFUND_WINDOW_DAYS) return { ok: false, reason: 'window-closed' };
  const balance = invoice.totalPence - invoice.refundedPence;
  if (requestedPence > balance) return { ok: false, reason: 'exceeds-balance' };
  return { ok: true, amountPence: requestedPence };
}
EOF
w src/billing/refunds.test.ts <<'EOF'
import { describe, expect, it } from 'vitest';
import type { Invoice } from './invoiceRepository';
import { decideRefund } from './refunds';

const now = new Date('2026-03-31T12:00:00Z');

function paidInvoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: 'inv_1',
    number: 'INV-001',
    customerId: 'cus_1',
    status: 'paid',
    totalPence: 10_000,
    refundedPence: 0,
    paidAt: new Date('2026-03-25T12:00:00Z'),
    ...overrides,
  };
}

describe('decideRefund', () => {
  it('refunds part of a paid invoice', () => {
    expect(decideRefund(paidInvoice(), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('still refunds on the thirtieth day after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-03-01T12:00:00Z') }), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('refuses a refund thirty-one days after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-02-28T12:00:00Z') }), 2_500, now)).toEqual({ ok: false, reason: 'window-closed' });
  });

  it('refunds exactly the balance left after earlier refunds', () => {
    expect(decideRefund(paidInvoice({ refundedPence: 8_000 }), 2_000, now)).toEqual({ ok: true, amountPence: 2_000 });
  });

  it('refuses more than the balance left after earlier refunds', () => {
    expect(decideRefund(paidInvoice({ refundedPence: 8_000 }), 2_001, now)).toEqual({ ok: false, reason: 'exceeds-balance' });
  });
});
EOF
commit 75 "feat: partial refunds against the remaining balance"

w src/billing/refunds.ts <<'EOF'
import type { Invoice } from './invoiceRepository';

export const REFUND_WINDOW_DAYS = 30;

export type RefundDecision =
  | { ok: true; amountPence: number }
  | { ok: false; reason: 'window-closed' | 'exceeds-balance' };

export function decideRefund(invoice: Invoice, requestedPence: number, now: Date): RefundDecision {
  const ageDays = (now.getTime() - (invoice.paidAt ?? now).getTime()) / 86_400_000;
  if (ageDays > REFUND_WINDOW_DAYS) return { ok: false, reason: 'window-closed' };
  const balance = invoice.totalPence - invoice.refundedPence;
  if (requestedPence <= 0 || requestedPence > balance) return { ok: false, reason: 'exceeds-balance' };
  return { ok: true, amountPence: requestedPence };
}
EOF
w src/billing/refunds.test.ts <<'EOF'
import { describe, expect, it } from 'vitest';
import type { Invoice } from './invoiceRepository';
import { decideRefund } from './refunds';

const now = new Date('2026-03-31T12:00:00Z');

function paidInvoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: 'inv_1',
    number: 'INV-001',
    customerId: 'cus_1',
    status: 'paid',
    totalPence: 10_000,
    refundedPence: 0,
    paidAt: new Date('2026-03-25T12:00:00Z'),
    ...overrides,
  };
}

describe('decideRefund', () => {
  it('refunds part of a paid invoice', () => {
    expect(decideRefund(paidInvoice(), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('still refunds on the thirtieth day after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-03-01T12:00:00Z') }), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('refuses a refund thirty-one days after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-02-28T12:00:00Z') }), 2_500, now)).toEqual({ ok: false, reason: 'window-closed' });
  });

  it('refunds exactly the balance left after earlier refunds', () => {
    expect(decideRefund(paidInvoice({ refundedPence: 8_000 }), 2_000, now)).toEqual({ ok: true, amountPence: 2_000 });
  });

  it('refuses more than the balance left after earlier refunds', () => {
    expect(decideRefund(paidInvoice({ refundedPence: 8_000 }), 2_001, now)).toEqual({ ok: false, reason: 'exceeds-balance' });
  });

  it('refuses a zero refund', () => {
    expect(decideRefund(paidInvoice(), 0, now)).toEqual({ ok: false, reason: 'exceeds-balance' });
  });
});
EOF
commit 55 "fix: refuse zero and negative refunds"

w src/ui/InvoiceList.tsx <<'EOF'
import { useState } from 'react';
import { formatPence } from '../billing/money';

export type InvoiceSummary = { id: string; number: string; status: 'draft' | 'sent' | 'paid' | 'void'; totalPence: number };

export function InvoiceList({ invoices, onSelect }: { invoices: InvoiceSummary[]; onSelect: (id: string) => void }) {
  const [paidOnly, setPaidOnly] = useState(false);
  const shown = paidOnly ? invoices.filter((invoice) => invoice.status === 'paid') : invoices;
  return (
    <section>
      <label>
        <input type="checkbox" checked={paidOnly} onChange={(event) => setPaidOnly(event.target.checked)} /> Show paid only
      </label>
      <table>
        <thead>
          <tr><th>Invoice</th><th>Status</th><th>Total</th></tr>
        </thead>
        <tbody>
          {shown.map((invoice) => (
            <tr key={invoice.id}>
              <td><button type="button" onClick={() => onSelect(invoice.id)}>{invoice.number}</button></td>
              <td>
                <span className={invoice.status === 'paid' ? 'badge bg-green-100 text-green-800' : 'badge bg-gray-100 text-gray-700'}>
                  {invoice.status}
                </span>
              </td>
              <td>{formatPence(invoice.totalPence)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
EOF
w src/ui/App.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { InvoiceList, type InvoiceSummary } from './InvoiceList';

export function App({ customerId }: { customerId: string }) {
  const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    fetch(new URL(`/api/customers/${customerId}/invoices`, window.location.origin))
      .then((response) => response.json())
      .then(setInvoices);
  }, [customerId]);

  return (
    <main>
      <h1>Invoices</h1>
      <InvoiceList invoices={invoices} onSelect={setSelected} />
      {selected && <p>Selected {selected}</p>}
    </main>
  );
}
EOF
w src/ui/main.tsx <<'EOF'
import { createRoot } from 'react-dom/client';
import { App } from './App';

const customerId = new URLSearchParams(window.location.search).get('customer') ?? '';
createRoot(document.getElementById('root')!).render(<App customerId={customerId} />);
EOF
w src/ui/InvoiceList.test.tsx <<'EOF'
// @vitest-environment jsdom
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { InvoiceList, type InvoiceSummary } from './InvoiceList';

const invoices: InvoiceSummary[] = [
  { id: 'inv_1', number: 'INV-001', status: 'paid', totalPence: 12_000 },
  { id: 'inv_2', number: 'INV-002', status: 'sent', totalPence: 4_550 },
];

describe('InvoiceList', () => {
  it('shows each invoice with its status and total in pounds', () => {
    render(<InvoiceList invoices={invoices} onSelect={() => {}} />);
    const row = screen.getByRole('row', { name: /INV-002/ });
    expect(within(row).getByText('sent')).toBeInTheDocument();
    expect(within(row).getByText('£45.50')).toBeInTheDocument();
  });

  it('hides unpaid invoices when the user asks for paid ones only', async () => {
    const user = userEvent.setup();
    render(<InvoiceList invoices={invoices} onSelect={() => {}} />);
    await user.click(screen.getByRole('checkbox', { name: 'Show paid only' }));
    expect(screen.getByRole('button', { name: 'INV-001' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'INV-002' })).not.toBeInTheDocument();
  });

  it('opens the invoice the user picks', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<InvoiceList invoices={invoices} onSelect={onSelect} />);
    await user.click(screen.getByRole('button', { name: 'INV-002' }));
    expect(onSelect).toHaveBeenCalledWith('inv_2');
  });
});
EOF
w src/ui/App.test.tsx <<'EOF'
// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { App } from './App';

const api = setupServer(
  http.get('*/api/customers/:customerId/invoices', ({ params }) =>
    HttpResponse.json(
      params.customerId === 'cus_1' ? [{ id: 'inv_1', number: 'INV-001', status: 'paid', totalPence: 12_000 }] : [],
    ),
  ),
);

beforeAll(() => api.listen({ onUnhandledRequest: 'error' }));
afterAll(() => api.close());

describe('App', () => {
  it("loads the customer's invoices and shows the one the user opens", async () => {
    const user = userEvent.setup();
    render(<App customerId="cus_1" />);
    await user.click(await screen.findByRole('button', { name: 'INV-001' }));
    expect(screen.getByText('Selected inv_1')).toBeInTheDocument();
  });
});
EOF
commit 45 "feat: admin invoice list"

w src/billing/refunds.ts <<'EOF'
import type { Invoice } from './invoiceRepository';

export const REFUND_WINDOW_DAYS = 30;

export type RefundDecision =
  | { ok: true; amountPence: number }
  | { ok: false; reason: 'not-paid' | 'window-closed' | 'exceeds-balance' };

export function decideRefund(invoice: Invoice, requestedPence: number, now: Date): RefundDecision {
  if (invoice.status !== 'paid' || !invoice.paidAt) return { ok: false, reason: 'not-paid' };
  const ageDays = (now.getTime() - invoice.paidAt.getTime()) / 86_400_000;
  if (ageDays > REFUND_WINDOW_DAYS) return { ok: false, reason: 'window-closed' };
  const balance = invoice.totalPence - invoice.refundedPence;
  if (requestedPence <= 0 || requestedPence > balance) return { ok: false, reason: 'exceeds-balance' };
  return { ok: true, amountPence: requestedPence };
}
EOF
w src/billing/refunds.test.ts <<'EOF'
import { describe, expect, it } from 'vitest';
import type { Invoice } from './invoiceRepository';
import { decideRefund } from './refunds';

const now = new Date('2026-03-31T12:00:00Z');

function paidInvoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: 'inv_1',
    number: 'INV-001',
    customerId: 'cus_1',
    status: 'paid',
    totalPence: 10_000,
    refundedPence: 0,
    paidAt: new Date('2026-03-25T12:00:00Z'),
    ...overrides,
  };
}

describe('decideRefund', () => {
  it('refunds part of a paid invoice', () => {
    expect(decideRefund(paidInvoice(), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('still refunds on the thirtieth day after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-03-01T12:00:00Z') }), 2_500, now)).toEqual({ ok: true, amountPence: 2_500 });
  });

  it('refuses a refund thirty-one days after payment', () => {
    expect(decideRefund(paidInvoice({ paidAt: new Date('2026-02-28T12:00:00Z') }), 2_500, now)).toEqual({ ok: false, reason: 'window-closed' });
  });

  it('refunds exactly the balance left after earlier refunds', () => {
    expect(decideRefund(paidInvoice({ refundedPence: 8_000 }), 2_000, now)).toEqual({ ok: true, amountPence: 2_000 });
  });

  it('refuses more than the balance left after earlier refunds', () => {
    expect(decideRefund(paidInvoice({ refundedPence: 8_000 }), 2_001, now)).toEqual({ ok: false, reason: 'exceeds-balance' });
  });

  it('refuses a zero refund', () => {
    expect(decideRefund(paidInvoice(), 0, now)).toEqual({ ok: false, reason: 'exceeds-balance' });
  });

  it('refuses to refund an invoice that was sent but never paid', () => {
    expect(decideRefund(paidInvoice({ status: 'sent', paidAt: null }), 2_500, now)).toEqual({ ok: false, reason: 'not-paid' });
  });
});
EOF
commit 30 "fix: refuse refunds on invoices that were never paid"

w src/billing/invoiceTotals.ts <<'EOF'
export type InvoiceLine = { description: string; unitPence: number; quantity: number; taxRate: number };

export type Discount = { kind: 'percent'; percent: number } | { kind: 'fixed'; pence: number };

export type InvoiceTotals = { subtotal: number; discount: number; tax: number; total: number };

export function lineSubtotal(line: InvoiceLine): number {
  return line.unitPence * line.quantity;
}

export function invoiceTotals(lines: InvoiceLine[], discount?: Discount): InvoiceTotals {
  const subtotal = lines.reduce((sum, line) => sum + lineSubtotal(line), 0);
  const discountPence = !discount
    ? 0
    : discount.kind === 'percent'
      ? Math.round((subtotal * discount.percent) / 100)
      : Math.min(discount.pence, subtotal);
  const ratio = subtotal === 0 ? 0 : (subtotal - discountPence) / subtotal;
  const tax = lines.reduce((sum, line) => sum + Math.round(lineSubtotal(line) * ratio * line.taxRate), 0);
  return { subtotal, discount: discountPence, tax, total: subtotal - discountPence + tax };
}
EOF
cat >> src/billing/invoiceTotals.test.ts <<'EOF'

describe('discounts', () => {
  it('spreads a percentage discount across taxed and untaxed lines', () => {
    expect(invoiceTotals([consulting, books], { kind: 'percent', percent: 10 })).toEqual({
      subtotal: 33_000,
      discount: 3_300,
      tax: 5_400,
      total: 35_100,
    });
  });

  it('never discounts more than the subtotal', () => {
    expect(invoiceTotals([books], { kind: 'fixed', pence: 5_000 })).toEqual({ subtotal: 3_000, discount: 3_000, tax: 0, total: 0 });
  });
});
EOF
commit 10 "feat: percentage and fixed discounts on invoices"
