#!/usr/bin/env bash
# Builds a TypeScript invoicing service with a small React admin screen, whose tests mostly cannot fail.
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
    "lint": "eslint .",
    "test": "vitest run"
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
    "@testing-library/jest-dom": "6.6.4",
    "@testing-library/react": "16.3.0",
    "@types/pg": "8.15.5",
    "@types/react": "19.1.10",
    "@types/react-dom": "19.1.7",
    "@vitejs/plugin-react": "5.0.0",
    "eslint": "9.33.0",
    "jsdom": "26.1.0",
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
    coverage: { provider: 'v8', reporter: ['text', 'html'] },
  },
});
EOF
w eslint.config.js <<'EOF'
import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'coverage'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
);
EOF
w .github/workflows/ci.yml <<'EOF'
name: ci
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm }
      - run: npm ci
      - run: npm run lint
      - run: npm run build
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
import { httpMailer } from '../notifications/mailer';
import { buildApp } from './app';

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
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
w src/billing/invoiceTotals.test.ts <<'EOF'
import { describe, expect, it } from 'vitest';
import { invoiceTotals, type InvoiceLine } from './invoiceTotals';

const lines: InvoiceLine[] = [
  { description: 'Consulting', unitPence: 10_000, quantity: 3, taxRate: 0.2 },
  { description: 'Books', unitPence: 1_500, quantity: 2, taxRate: 0 },
];

describe('invoiceTotals', () => {
  it('calculates the total for an invoice', () => {
    expect(invoiceTotals(lines).total).toEqual(invoiceTotals(lines).total);
  });

  it('handles an empty invoice', () => {
    invoiceTotals([]);
  });

  it('charges tax on taxable lines', () => {
    expect(invoiceTotals(lines).tax).not.toBe(0);
  });

  it('adds up the line subtotals', () => {
    expect(invoiceTotals(lines).subtotal).toBe(33_000);
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
import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { InvoiceList, type InvoiceSummary } from './InvoiceList';

const invoices: InvoiceSummary[] = [
  { id: 'inv_1', number: 'INV-001', status: 'paid', totalPence: 12_000 },
  { id: 'inv_2', number: 'INV-002', status: 'sent', totalPence: 4_550 },
];

describe('InvoiceList', () => {
  it('renders the invoice list', () => {
    const { container } = render(<InvoiceList invoices={invoices} onSelect={() => {}} />);
    expect(container).toMatchSnapshot();
  });

  it('marks paid invoices', () => {
    const { container } = render(<InvoiceList invoices={invoices} onSelect={() => {}} />);
    expect(container.querySelector('.badge')).toHaveClass('bg-green-100');
  });

  it('selects an invoice', () => {
    const onSelect = vi.fn();
    const { getByText } = render(<InvoiceList invoices={invoices} onSelect={onSelect} />);
    fireEvent.click(getByText('INV-001'));
    expect(onSelect).toHaveBeenCalled();
  });
});
EOF
w src/ui/__snapshots__/InvoiceList.test.tsx.snap <<'EOF'
// Vitest Snapshot v1, https://vitest.dev/guide/snapshot.html

exports[`InvoiceList > renders the invoice list 1`] = `
<div>
  <section>
    <label>
      <input
        type="checkbox"
      />
       Show paid only
    </label>
    <table>
      <thead>
        <tr>
          <th>
            Invoice
          </th>
          <th>
            Status
          </th>
          <th>
            Total
          </th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>
            <button
              type="button"
            >
              INV-001
            </button>
          </td>
          <td>
            <span
              class="badge bg-green-100 text-green-800"
            >
              paid
            </span>
          </td>
          <td>
            £120.00
          </td>
        </tr>
        <tr>
          <td>
            <button
              type="button"
            >
              INV-002
            </button>
          </td>
          <td>
            <span
              class="badge bg-gray-100 text-gray-700"
            >
              sent
            </span>
          </td>
          <td>
            £45.50
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</div>
`;
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
commit 30 "fix: refuse refunds on invoices that were never paid"

w src/server/app.test.ts <<'EOF'
import { describe, expect, it, vi } from 'vitest';
import type { Invoice } from '../billing/invoiceRepository';
import { decideRefund } from '../billing/refunds';
import { buildApp } from './app';

vi.mock('../billing/refunds', () => ({ decideRefund: vi.fn() }));

const sentInvoice: Invoice = {
  id: 'inv_2',
  number: 'INV-002',
  customerId: 'cus_1',
  status: 'sent',
  totalPence: 4_550,
  refundedPence: 0,
  paidAt: null,
};

function fakeDependencies() {
  return {
    invoices: { find: vi.fn(), listForCustomer: vi.fn(), recordRefund: vi.fn() },
    mailer: { send: vi.fn() },
    now: () => new Date('2026-03-10T12:00:00Z'),
  };
}

describe('invoice routes', () => {
  it('returns an invoice', async () => {
    const deps = fakeDependencies();
    deps.invoices.find.mockResolvedValue(sentInvoice);
    const response = await buildApp(deps).inject({ method: 'GET', url: '/invoices/inv_2' });
    expect(response.json()).toEqual(sentInvoice);
  });

  it('refunds a paid invoice', async () => {
    const deps = fakeDependencies();
    deps.invoices.find.mockResolvedValue({ ...sentInvoice, status: 'paid', paidAt: new Date('2026-03-01T12:00:00Z') });
    vi.mocked(decideRefund).mockReturnValue({ ok: true, amountPence: 500 });
    await buildApp(deps).inject({
      method: 'POST',
      url: '/invoices/inv_2/refunds',
      payload: { amountPence: 500, email: 'ada@example.com' },
    });
    expect(decideRefund).toHaveBeenCalled();
    expect(deps.invoices.recordRefund).toHaveBeenCalled();
    expect(deps.mailer.send).toHaveBeenCalled();
  });
});
EOF
w src/notifications/mailer.test.ts <<'EOF'
import { describe, expect, it } from 'vitest';
import { refundReceipt } from './mailer';

describe('refundReceipt', () => {
  it.only('addresses the receipt to the customer', () => {
    expect(refundReceipt('ada@example.com', 'inv_7', 1_250).to).toBe('ada@example.com');
  });

  it('states the refunded amount', () => {
    expect(refundReceipt('ada@example.com', 'inv_7', 1_250).text).toContain('£12.50');
  });
});
EOF
commit 20 "test: cover invoice routes and refund receipts"

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
  it('applies a percentage discount', () => {
    const expected = invoiceTotals(lines, { kind: 'percent', percent: 10 });
    expect(invoiceTotals(lines, { kind: 'percent', percent: 10 })).toEqual(expected);
  });

  it('caps a fixed discount at the subtotal', () => {
    const totals = invoiceTotals(lines, { kind: 'fixed', pence: 50_000 });
    if (totals.discount > totals.subtotal) {
      expect(totals.discount).toBe(totals.subtotal);
    }
  });
});
EOF
commit 10 "feat: percentage and fixed discounts on invoices"
