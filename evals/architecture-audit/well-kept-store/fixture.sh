#!/usr/bin/env bash
# Builds the same storefront structured well, to measure false positives.
set -euo pipefail
export GIT_AUTHOR_NAME=Fixture GIT_AUTHOR_EMAIL=fixture@example.com GIT_COMMITTER_NAME=Fixture GIT_COMMITTER_EMAIL=fixture@example.com
git init -q
w() { mkdir -p "$(dirname "$1")"; cat > "$1"; }
commit() { local days="$1"; shift; local when; when="$(date -u -v-"${days}"d +%Y-%m-%dT12:00:00Z 2>/dev/null || date -u -d "-${days} days" +%Y-%m-%dT12:00:00Z)"; git add -A; GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" git commit -qm "$*"; }

w package.json <<'EOF'
{
  "name": "storefront",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "lint": "next lint && depcruise src --config .dependency-cruiser.js",
    "test": "vitest run"
  },
  "dependencies": { "@tanstack/react-query": "5.40.0", "next": "14.2.4", "react": "18.3.1", "react-dom": "18.3.1", "zod": "3.23.8", "zustand": "4.5.2" },
  "devDependencies": { "dependency-cruiser": "16.3.3", "typescript": "5.5.2", "vitest": "1.6.0" }
}
EOF
w tsconfig.json <<'EOF'
{ "compilerOptions": { "strict": true, "jsx": "preserve", "baseUrl": ".", "paths": { "@/*": ["src/*"] } } }
EOF
w .dependency-cruiser.js <<'EOF'
module.exports = {
  options: { tsConfig: { fileName: 'tsconfig.json' } },
  forbidden: [
    { name: 'not-to-unresolvable', severity: 'error', from: {}, to: { couldNotResolve: true } },
    { name: 'no-circular', severity: 'error', from: {}, to: { circular: true } },
    { name: 'features-through-entry-points', severity: 'error', from: { path: '^src/features/([^/]+)/' }, to: { path: '^src/features/(?!$1)[^/]+/(?!index\\.ts$)' } },
    { name: 'domain-stays-pure', severity: 'error', from: { path: '^src/domain/' }, to: { path: '^src/(features|app|components)/' } },
  ],
};
EOF
w .github/workflows/ci.yml <<'EOF'
name: ci
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: npm run lint
      - run: npm test
EOF
w docs/decisions/0001-server-state-in-react-query.md <<'EOF'
# 0001 — Server state lives in React Query

Status: accepted. Server data is fetched only through hooks in `src/api`, which parse responses into domain types.
EOF
w GLOSSARY.md <<'EOF'
# Glossary

- **Customer:** the person placing an order. Never "client" or "user" in domain code.
- **Line:** one product and quantity in a cart or order.
EOF
w src/domain/money.ts <<'EOF'
export type Money = { pence: number };

export const TAX_RATE = 0.2;

export function addTax(amount: Money, rate: number): Money {
  return { pence: Math.round(amount.pence * (1 + rate)) };
}

export function formatMoney(amount: Money): string {
  return `£${(amount.pence / 100).toFixed(2)}`;
}
EOF
w src/domain/order.ts <<'EOF'
import type { Money } from './money';

export type Line = { sku: string; quantity: number; unitPrice: Money };
export type Order = { id: string; customerId: string; lines: Line[]; placedAt: Date };
EOF
w src/api/client.ts <<'EOF'
export async function getJson(path: string): Promise<unknown> {
  const response = await fetch(`/api${path}`);
  if (!response.ok) throw new Error(`Request to ${path} failed with ${response.status}`);
  return response.json();
}
EOF
w src/api/orders.ts <<'EOF'
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import type { Order } from '@/domain/order';
import { getJson } from './client';

const orderSchema = z.object({
  order_id: z.string(),
  customer_ref: z.string(),
  created_at: z.string(),
  line_items: z.array(z.object({ sku: z.string(), qty: z.number(), unit_price_cents: z.number() })),
});

export function toOrder(raw: unknown): Order {
  const parsed = orderSchema.parse(raw);
  return {
    id: parsed.order_id,
    customerId: parsed.customer_ref,
    placedAt: new Date(parsed.created_at),
    lines: parsed.line_items.map((item) => ({ sku: item.sku, quantity: item.qty, unitPrice: { pence: item.unit_price_cents } })),
  };
}

export function useOrders() {
  return useQuery({ queryKey: ['orders'], queryFn: async () => ((await getJson('/orders')) as unknown[]).map(toOrder) });
}
EOF
w src/features/cart/index.ts <<'EOF'
export { CartView } from './CartView';
export { useCart } from './useCart';
EOF
w src/features/cart/cartStore.ts <<'EOF'
import { create } from 'zustand';
import type { Line } from '@/domain/order';
import { loadLines, saveLines } from './cartStorage';

type CartState = { lines: Line[]; add: (line: Line) => void; remove: (sku: string) => void };

export const cartStore = create<CartState>((set, get) => ({
  lines: loadLines(),
  add: (line) => { const lines = [...get().lines, line]; saveLines(lines); set({ lines }); },
  remove: (sku) => { const lines = get().lines.filter((line) => line.sku !== sku); saveLines(lines); set({ lines }); },
}));
EOF
w src/features/cart/cartStorage.ts <<'EOF'
import type { Line } from '@/domain/order';

const KEY = 'cart:v2';

export function loadLines(): Line[] {
  if (typeof window === 'undefined') return [];
  const saved = window.localStorage.getItem(KEY);
  return saved ? (JSON.parse(saved) as Line[]) : [];
}

export function saveLines(lines: Line[]): void {
  window.localStorage.setItem(KEY, JSON.stringify(lines));
}
EOF
w src/features/cart/useCart.ts <<'EOF'
import { cartStore } from './cartStore';

export function useCart() {
  return cartStore();
}
EOF
w src/features/cart/CartView.tsx <<'EOF'
'use client';
import { formatMoney, TAX_RATE } from '@/domain/money';
import { checkoutTotal } from '@/features/checkout';
import { useCart } from './useCart';

export function CartView() {
  const { lines, remove } = useCart();
  return (
    <section>
      {lines.map((line) => <button key={line.sku} onClick={() => remove(line.sku)}>{line.sku}</button>)}
      <p>{formatMoney(checkoutTotal(lines, TAX_RATE))}</p>
    </section>
  );
}
EOF
w src/features/checkout/index.ts <<'EOF'
export { checkoutTotal } from './checkoutTotal';
EOF
w src/features/checkout/checkoutTotal.ts <<'EOF'
import { addTax, type Money } from '@/domain/money';
import type { Line } from '@/domain/order';

export function checkoutTotal(lines: Line[], taxRate: number): Money {
  return lines.reduce((sum, line) => ({ pence: sum.pence + addTax(line.unitPrice, taxRate).pence * line.quantity }), { pence: 0 });
}
EOF
w src/features/checkout/checkoutTotal.test.ts <<'EOF'
import { describe, expect, it } from 'vitest';
import * as checkout from './checkoutTotal';
import { checkoutTotal } from './checkoutTotal';

describe('checkoutTotal', () => {
  it('adds tax to every line', () => {
    expect(checkoutTotal([{ sku: 'a', quantity: 2, unitPrice: { pence: 1000 } }], 0.2)).toEqual({ pence: 2400 });
  });
});
EOF
w src/features/orders/OrderHistory.tsx <<'EOF'
'use client';
import { useOrders } from '@/api/orders';

export function OrderHistory() {
  const { data = [] } = useOrders();
  return <ul>{data.map((order) => <li key={order.id}>{order.customerId}: {order.lines.length} lines</li>)}</ul>;
}
EOF
w src/features/orders/index.ts <<'EOF'
export { OrderHistory } from './OrderHistory';
EOF
w src/app/page.tsx <<'EOF'
import { CartView } from '@/features/cart';
import { OrderHistory } from '@/features/orders';

export default function Home() {
  return <main><CartView /><OrderHistory /></main>;
}
EOF
commit 120 "feat: storefront with cart, checkout, and orders"
for n in 1 2 3 4; do
  cat >> src/features/checkout/checkoutTotal.ts <<EOF

export const FREE_DELIVERY_THRESHOLD_$n: Money = { pence: $((n * 2500)) };
EOF
  cat >> src/features/checkout/checkoutTotal.test.ts <<EOF

it('sets free delivery threshold $n at £$((n * 25))', () => {
  expect(checkout.FREE_DELIVERY_THRESHOLD_$n).toEqual({ pence: $((n * 2500)) });
});
EOF
  commit $((100 - n * 10)) "feat: free delivery threshold $n"
done
