#!/usr/bin/env bash
# Builds a Next.js 16 App Router orders dashboard on React 19 with a tangled client layer.
set -euo pipefail
export GIT_AUTHOR_NAME=Fixture GIT_AUTHOR_EMAIL=fixture@example.com GIT_COMMITTER_NAME=Fixture GIT_COMMITTER_EMAIL=fixture@example.com
git init -q
w() { mkdir -p "$(dirname "$1")"; cat > "$1"; }
commit() { local days="$1"; shift; local when; when="$(date -u -v-"${days}"d +%Y-%m-%dT12:00:00Z 2>/dev/null || date -u -d "-${days} days" +%Y-%m-%dT12:00:00Z)"; git add -A; GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" git commit -qm "$*"; }

w package.json <<'EOF'
{
  "name": "orders-dashboard",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "next": "16.4.0",
    "pg": "^8.16.3",
    "react": "19.3.0",
    "react-dom": "19.3.0",
    "stripe": "^18.5.0"
  },
  "devDependencies": {
    "@eslint/js": "^9",
    "@next/eslint-plugin-next": "16.4.0",
    "@types/node": "^22",
    "@types/pg": "^8",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "eslint": "^9",
    "typescript": "^5",
    "typescript-eslint": "^8"
  }
}
EOF
w tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "strict": true,
    "noEmit": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",
    "incremental": true,
    "skipLibCheck": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
EOF
w next.config.ts <<'EOF'
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
};

export default nextConfig;
EOF
w eslint.config.mjs <<'EOF'
import { defineConfig, globalIgnores } from "eslint/config";
import js from "@eslint/js";
import nextPlugin from "@next/eslint-plugin-next";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores([".next/**", "next-env.d.ts"]),
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "@next/next": nextPlugin },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs["core-web-vitals"].rules,
    },
  },
]);
EOF
w .gitignore <<'EOF'
node_modules
.next
next-env.d.ts
.env*.local
EOF
w .env.example <<'EOF'
DATABASE_URL=postgres://localhost:5432/orders
STRIPE_SECRET_KEY=
EOF
w src/app/globals.css <<'EOF'
body { font-family: system-ui, sans-serif; margin: 0; color: #1f2933; }
main { max-width: 64rem; margin: 0 auto; padding: 2rem; }
table { width: 100%; border-collapse: collapse; }
td, th { padding: 0.5rem; border-bottom: 1px solid #e4e7eb; text-align: left; }
EOF
w src/app/layout.tsx <<'EOF'
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Orders" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
EOF
w src/app/page.tsx <<'EOF'
import { redirect } from "next/navigation";

export default function Home() {
  redirect("/orders");
}
EOF
w src/lib/types.ts <<'EOF'
export type OrderStatus = "pending" | "paid" | "refunded" | "partially_refunded";

export type OrderLine = {
  sku: string;
  description: string;
  quantity: number;
  unitCents: number;
};

export type Order = {
  id: string;
  number: string;
  customerName: string;
  customerEmail: string;
  status: OrderStatus;
  currency: string;
  lines: OrderLine[];
  refundedCents: number;
  paymentIntentId: string;
  createdAt: string;
};
EOF
w src/lib/db.ts <<'EOF'
import { Pool } from "pg";

export const pool = new Pool({ connectionString: process.env.DATABASE_URL });
EOF
w src/lib/orders.ts <<'EOF'
import { pool } from "./db";
import type { Order } from "./types";

const columns = `id, number, customer_name AS "customerName", customer_email AS "customerEmail", status, currency,
  lines, refunded_cents AS "refundedCents", payment_intent_id AS "paymentIntentId", created_at AS "createdAt"`;

export async function listOrders(): Promise<Order[]> {
  const result = await pool.query<Order>(`SELECT ${columns} FROM orders ORDER BY created_at DESC LIMIT 100`);
  return result.rows;
}

export async function searchOrders(query: string): Promise<Order[]> {
  const result = await pool.query<Order>(
    `SELECT ${columns} FROM orders WHERE number ILIKE $1 OR customer_name ILIKE $1 ORDER BY created_at DESC LIMIT 20`,
    [`%${query}%`],
  );
  return result.rows;
}

export async function getOrder(id: string): Promise<Order | null> {
  const result = await pool.query<Order>(`SELECT ${columns} FROM orders WHERE id = $1`, [id]);
  return result.rows[0] ?? null;
}

export async function recordRefund(id: string, cents: number): Promise<void> {
  await pool.query(
    `UPDATE orders SET refunded_cents = refunded_cents + $2,
       status = CASE WHEN refunded_cents + $2 >= total_cents THEN 'refunded' ELSE 'partially_refunded' END
     WHERE id = $1`,
    [id, cents],
  );
}
EOF
w src/lib/payments.ts <<'EOF'
import Stripe from "stripe";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY ?? "");

export function formatAmount(cents: number, currency: string): string {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(cents / 100);
}

export async function refundPayment(paymentIntentId: string, cents: number): Promise<string> {
  const refund = await stripe.refunds.create({ payment_intent: paymentIntentId, amount: cents });
  return refund.id;
}
EOF
w src/lib/analytics.ts <<'EOF'
export function trackView(orderId: string, plan: string): void {
  navigator.sendBeacon("/api/analytics", JSON.stringify({ event: "order_viewed", orderId, plan }));
}
EOF
w src/components/ui/Button.tsx <<'EOF'
import { forwardRef } from "react";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" };

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({ variant = "primary", ...props }, ref) {
  return <button ref={ref} data-variant={variant} {...props} />;
});
EOF
w src/components/ui/Input.tsx <<'EOF'
import { forwardRef } from "react";

type InputProps = React.InputHTMLAttributes<HTMLInputElement> & { label: string };

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ label, id, ...props }, ref) {
  return (
    <label htmlFor={id}>
      {label}
      <input ref={ref} id={id} {...props} />
    </label>
  );
});
EOF
w src/components/OrdersTable.tsx <<'EOF'
"use client";

import { useEffect, useState } from "react";
import type { Order } from "@/lib/types";
import { Button } from "./ui/Button";

type OrdersTableProps = {
  orders: Order[];
  onExport: (orders: Order[]) => string;
};

export default function OrdersTable({ orders, onExport }: OrdersTableProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);

  useEffect(() => {
    setSelectedOrder(orders.find((order) => order.id === selectedId) ?? null);
  }, [selectedId]);

  function download() {
    const csv = onExport(orders);
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "orders.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section>
      <Button variant="secondary" onClick={download}>Export CSV</Button>
      <table>
        <thead>
          <tr><th>Order</th><th>Customer</th><th>Status</th></tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr key={order.id} aria-selected={order.id === selectedId} onClick={() => setSelectedId(order.id)}>
              <td>{order.number}</td>
              <td>{order.customerName}</td>
              <td>{order.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {selectedOrder && (
        <aside>
          <h2>{selectedOrder.number}</h2>
          <p>{selectedOrder.customerEmail}</p>
          <a href={`/orders/${selectedOrder.id}`}>Open order</a>
        </aside>
      )}
    </section>
  );
}
EOF
w src/components/OrderSearch.tsx <<'EOF'
"use client";

import { useEffect, useState } from "react";
import type { Order } from "@/lib/types";
import { Input } from "./ui/Input";

export function OrderSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Order[]>([]);

  useEffect(() => {
    if (query.length < 2) {
      setResults([]);
      return;
    }
    fetch(`/api/orders?q=${encodeURIComponent(query)}`)
      .then((response) => response.json())
      .then((data: Order[]) => setResults(data));
  }, [query]);

  return (
    <div>
      <Input id="order-search" label="Search orders" value={query} onChange={(event) => setQuery(event.target.value)} />
      <ul>
        {results.map((order) => (
          <li key={order.id}>
            <a href={`/orders/${order.id}`}>{order.number} · {order.customerName}</a>
          </li>
        ))}
      </ul>
    </div>
  );
}
EOF
w src/app/api/orders/route.ts <<'EOF'
import { NextResponse, type NextRequest } from "next/server";
import { searchOrders } from "@/lib/orders";

export async function GET(request: NextRequest) {
  const query = request.nextUrl.searchParams.get("q") ?? "";
  return NextResponse.json(await searchOrders(query));
}
EOF
w src/app/orders/page.tsx <<'EOF'
import OrdersTable from "@/components/OrdersTable";
import { OrderSearch } from "@/components/OrderSearch";
import { listOrders } from "@/lib/orders";
import type { Order } from "@/lib/types";

function exportCsv(orders: Order[]): string {
  const header = "number,customer,status,created";
  const rows = orders.map((order) => [order.number, order.customerName, order.status, order.createdAt].join(","));
  return [header, ...rows].join("\n");
}

export default async function OrdersPage() {
  const orders = await listOrders();
  return (
    <main>
      <h1>Orders</h1>
      <OrderSearch />
      <OrdersTable orders={orders} onExport={exportCsv} />
    </main>
  );
}
EOF
commit 40 "feat: orders list with search and CSV export"

w src/components/OrderSummary.tsx <<'EOF'
"use client";

import { useEffect, useState } from "react";
import type { OrderLine } from "@/lib/types";

type OrderSummaryProps = {
  lines: OrderLine[];
  refundedCents: number;
  format: (cents: number) => string;
};

export function OrderSummary({ lines, refundedCents, format }: OrderSummaryProps) {
  const [total, setTotal] = useState(0);
  const [refundable, setRefundable] = useState(0);

  useEffect(() => {
    const sum = lines.reduce((running, line) => running + line.quantity * line.unitCents, 0);
    setTotal(sum);
    setRefundable(sum - refundedCents);
  }, [lines, refundedCents]);

  return (
    <dl>
      <dt>Total</dt>
      <dd>{format(total)}</dd>
      <dt>Refundable</dt>
      <dd>{format(refundable)}</dd>
    </dl>
  );
}
EOF
w src/components/OrderDetail.tsx <<'EOF'
"use client";

import { useEffect } from "react";
import { trackView } from "@/lib/analytics";
import { formatAmount } from "@/lib/payments";
import type { Order } from "@/lib/types";
import { OrderSummary } from "./OrderSummary";

type OrderDetailProps = {
  order: Order;
  plan: string;
};

export function OrderDetail({ order, plan }: OrderDetailProps) {
  useEffect(() => {
    trackView(order.id, plan);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order.id]);

  return (
    <article>
      <h1>{order.number}</h1>
      <p>
        {order.customerName} · {order.customerEmail}
      </p>
      <OrderSummary
        lines={order.lines}
        refundedCents={order.refundedCents}
        format={(cents) => formatAmount(cents, order.currency)}
      />
    </article>
  );
}
EOF
w src/app/orders/[id]/page.tsx <<'EOF'
import { notFound } from "next/navigation";
import { OrderDetail } from "@/components/OrderDetail";
import RefundForm from "@/components/RefundForm";
import { getOrder } from "@/lib/orders";

export default async function OrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const order = await getOrder(id);
  if (!order) notFound();
  return (
    <main>
      <OrderDetail order={order} plan="growth" />
      <RefundForm order={order} />
    </main>
  );
}
EOF
commit 30 "feat: order detail page with totals"

w src/app/api/orders/[id]/refund/route.ts <<'EOF'
import { NextResponse, type NextRequest } from "next/server";
import { getOrder, recordRefund } from "@/lib/orders";
import { refundPayment } from "@/lib/payments";

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { cents } = (await request.json()) as { cents: number };
  const order = await getOrder(id);
  if (!order) return NextResponse.json({ error: "Order not found" }, { status: 404 });
  const total = order.lines.reduce((sum, line) => sum + line.quantity * line.unitCents, 0);
  if (!Number.isInteger(cents) || cents <= 0 || cents > total - order.refundedCents) {
    return NextResponse.json({ error: "Refund exceeds the refundable amount" }, { status: 422 });
  }
  await refundPayment(order.paymentIntentId, cents);
  await recordRefund(order.id, cents);
  return NextResponse.json({ ok: true });
}
EOF
w src/components/RefundForm.tsx <<'EOF'
"use client";

import { useState } from "react";
import { formatAmount } from "@/lib/payments";
import type { Order } from "@/lib/types";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";

export default function RefundForm({ order }: { order: Order }) {
  const [amount, setAmount] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isError, setIsError] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    const cents = Math.round(Number(amount) * 100);
    const response = await fetch(`/api/orders/${order.id}/refund`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cents }),
    });
    setIsSubmitting(false);
    if (!response.ok) {
      const body = (await response.json()) as { error: string };
      setIsError(true);
      setErrorMessage(body.error);
      return;
    }
    setIsSuccess(true);
    setAmount("");
  }

  return (
    <form onSubmit={handleSubmit}>
      <h2>Refund</h2>
      <p>Already refunded: {formatAmount(order.refundedCents, order.currency)}</p>
      <Input id="refund-amount" label="Amount" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} />
      <Button type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Refunding…" : "Refund"}
      </Button>
      {isError && <p role="alert">{errorMessage}</p>}
      {isSuccess && <p role="status">Refund issued.</p>}
    </form>
  );
}
EOF
commit 21 "feat: refunds from the order page"

perl -0pi -e 's/  const \[errorMessage, setErrorMessage\] = useState\(""\);\n/  const [errorMessage, setErrorMessage] = useState("");\n  const [isOverLimit, setIsOverLimit] = useState(false);\n/' src/components/RefundForm.tsx
perl -0pi -e 's/    const cents = Math.round\(Number\(amount\) \* 100\);\n/    const cents = Math.round(Number(amount) * 100);\n    const refundable = order.lines.reduce((sum, line) => sum + line.quantity * line.unitCents, 0) - order.refundedCents;\n    if (cents > refundable) {\n      setIsOverLimit(true);\n      setIsSubmitting(false);\n      return;\n    }\n/' src/components/RefundForm.tsx
perl -0pi -e 's/      \{isSuccess && /      {isOverLimit && <p role="alert">That is more than the refundable amount.<\/p>}\n      {isSuccess && /' src/components/RefundForm.tsx
commit 12 "fix: stop refunds above the refundable amount before calling the API"

perl -0pi -e 's/<td>\{order.status\}<\/td>/<td>{order.status.replace("_", " ")}<\/td>/' src/components/OrdersTable.tsx
commit 6 "fix: readable order statuses"

perl -0pi -e 's/LIMIT 20/LIMIT 25/' src/lib/orders.ts
perl -0pi -e 's/if \(query.length < 2\)/if (query.trim().length < 2)/' src/components/OrderSearch.tsx
commit 2 "fix: ignore whitespace-only searches"
