#!/usr/bin/env bash
# Builds the same Next.js 16 orders dashboard on React 19, written with the compiler, actions, and a clean boundary.
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
    "server-only": "^0.0.1",
    "stripe": "^18.5.0",
    "zod": "^4.1.0"
  },
  "devDependencies": {
    "@eslint/js": "^9",
    "@next/eslint-plugin-next": "16.4.0",
    "@types/node": "^22",
    "@types/pg": "^8",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "babel-plugin-react-compiler": "1.0.0",
    "eslint": "^9",
    "eslint-plugin-react-hooks": "^7.1.1",
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
  reactCompiler: true,
};

export default nextConfig;
EOF
w eslint.config.mjs <<'EOF'
import { defineConfig, globalIgnores } from "eslint/config";
import js from "@eslint/js";
import nextPlugin from "@next/eslint-plugin-next";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const componentFiles = ["src/**/*.{ts,tsx}"];

export default defineConfig([
  globalIgnores([".next/**", "next-env.d.ts"]),
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: componentFiles,
    extends: [reactHooks.configs.flat.recommended],
  },
  {
    files: componentFiles,
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
STAFF_SIGN_IN_URL=https://sso.example.com/orders
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

export type OrderRow = Pick<Order, "id" | "number" | "customerName" | "customerEmail" | "status">;
EOF
w src/lib/money.ts <<'EOF'
import type { OrderLine } from "./types";

export function formatAmount(cents: number, currency: string): string {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(cents / 100);
}

export function orderTotal(lines: OrderLine[]): number {
  return lines.reduce((sum, line) => sum + line.quantity * line.unitCents, 0);
}
EOF
w src/lib/db.ts <<'EOF'
import "server-only";
import { Pool } from "pg";

export const pool = new Pool({ connectionString: process.env.DATABASE_URL });
EOF
w src/lib/orders.ts <<'EOF'
import "server-only";
import { pool } from "./db";
import type { Order, OrderRow } from "./types";

const columns = `id, number, customer_name AS "customerName", customer_email AS "customerEmail", status, currency,
  lines, refunded_cents AS "refundedCents", payment_intent_id AS "paymentIntentId", created_at AS "createdAt"`;

export async function searchOrders(query: string): Promise<OrderRow[]> {
  const result = await pool.query<OrderRow>(
    `SELECT id, number, customer_name AS "customerName", customer_email AS "customerEmail", status
       FROM orders WHERE $1 = '' OR number ILIKE $2 OR customer_name ILIKE $2
       ORDER BY created_at DESC LIMIT 100`,
    [query, `%${query}%`],
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
import "server-only";
import Stripe from "stripe";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY ?? "");

export async function refundPayment(paymentIntentId: string, cents: number): Promise<string> {
  const refund = await stripe.refunds.create({ payment_intent: paymentIntentId, amount: cents });
  return refund.id;
}
EOF
w src/lib/auth.ts <<'EOF'
import "server-only";
import { createHash } from "node:crypto";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { pool } from "./db";

export async function requireStaff(): Promise<{ staffId: string }> {
  const token = (await cookies()).get("staff_session")?.value;
  if (token) {
    const result = await pool.query<{ staffId: string }>(
      `SELECT staff_id AS "staffId" FROM staff_sessions WHERE token_hash = $1 AND expires_at > now()`,
      [createHash("sha256").update(token).digest("hex")],
    );
    if (result.rows[0]) return result.rows[0];
  }
  redirect(process.env.STAFF_SIGN_IN_URL ?? "/");
}
EOF
w src/lib/analytics.ts <<'EOF'
export function trackView(orderId: string, plan: string): void {
  navigator.sendBeacon("/api/analytics", JSON.stringify({ event: "order_viewed", orderId, plan }));
}
EOF
w src/components/ui/Button.tsx <<'EOF'
type ButtonProps = React.ComponentProps<"button"> & { variant?: "primary" | "secondary" };

export function Button({ variant = "primary", ref, ...props }: ButtonProps) {
  return <button ref={ref} data-variant={variant} {...props} />;
}
EOF
w src/components/ui/Input.tsx <<'EOF'
type InputProps = React.ComponentProps<"input"> & { label: string };

export function Input({ label, id, ref, ...props }: InputProps) {
  return (
    <label htmlFor={id}>
      {label}
      <input ref={ref} id={id} {...props} />
    </label>
  );
}
EOF
w src/components/SearchBox.tsx <<'EOF'
import Form from "next/form";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";

export function SearchBox({ query }: { query: string }) {
  return (
    <Form action="/orders" role="search" key={query}>
      <Input id="order-search" name="q" label="Search orders" type="search" defaultValue={query} />
      <Button type="submit" variant="secondary">Search</Button>
    </Form>
  );
}
EOF
w src/components/OrdersTable.tsx <<'EOF'
"use client";

import { useState } from "react";
import type { OrderRow } from "@/lib/types";
import { Button } from "./ui/Button";

type OrdersTableProps = {
  orders: OrderRow[];
  exportOrders: (query: string) => Promise<string>;
  query: string;
};

export function OrdersTable({ orders, exportOrders, query }: OrdersTableProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = orders.find((order) => order.id === selectedId) ?? null;

  async function download() {
    const csv = await exportOrders(query);
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
      {selected && (
        <aside>
          <h2>{selected.number}</h2>
          <p>{selected.customerEmail}</p>
          <a href={`/orders/${selected.id}`}>Open order</a>
        </aside>
      )}
    </section>
  );
}
EOF
w src/app/orders/actions.ts <<'EOF'
"use server";

import { requireStaff } from "@/lib/auth";
import { searchOrders } from "@/lib/orders";

function csvField(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
}

export async function exportOrders(query: string): Promise<string> {
  await requireStaff();
  const orders = await searchOrders(String(query).slice(0, 100));
  const rows = orders.map((order) => [order.number, order.customerName, order.status].map(csvField).join(","));
  return ["number,customer,status", ...rows].join("\n");
}
EOF
w src/app/orders/page.tsx <<'EOF'
import { OrdersTable } from "@/components/OrdersTable";
import { SearchBox } from "@/components/SearchBox";
import { requireStaff } from "@/lib/auth";
import { searchOrders } from "@/lib/orders";
import { exportOrders } from "./actions";

export default async function OrdersPage({ searchParams }: { searchParams: Promise<{ q?: string | string[] }> }) {
  await requireStaff();
  const { q } = await searchParams;
  const query = typeof q === "string" ? q.slice(0, 100) : "";
  const orders = await searchOrders(query);
  return (
    <main>
      <h1>Orders</h1>
      <SearchBox query={query} />
      <OrdersTable orders={orders} exportOrders={exportOrders} query={query} />
    </main>
  );
}
EOF
commit 40 "feat: orders list with URL search and CSV export"

w src/components/OrderSummary.tsx <<'EOF'
import { formatAmount, orderTotal } from "@/lib/money";
import type { OrderLine } from "@/lib/types";

type OrderSummaryProps = {
  lines: OrderLine[];
  refundedCents: number;
  currency: string;
};

export function OrderSummary({ lines, refundedCents, currency }: OrderSummaryProps) {
  const total = orderTotal(lines);
  return (
    <dl>
      <dt>Total</dt>
      <dd>{formatAmount(total, currency)}</dd>
      <dt>Refundable</dt>
      <dd>{formatAmount(total - refundedCents, currency)}</dd>
    </dl>
  );
}
EOF
w src/components/OrderViewTracker.tsx <<'EOF'
"use client";

import { useEffect, useEffectEvent } from "react";
import { trackView } from "@/lib/analytics";

export function OrderViewTracker({ orderId, plan }: { orderId: string; plan: string }) {
  const onView = useEffectEvent((viewedOrderId: string) => {
    trackView(viewedOrderId, plan);
  });

  useEffect(() => {
    onView(orderId);
  }, [orderId]);

  return null;
}
EOF
w src/components/OrderStatusPoller.tsx <<'EOF'
"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function OrderStatusPoller({ intervalMs }: { intervalMs: number }) {
  const router = useRouter();

  useEffect(() => {
    const id = setInterval(() => router.refresh(), intervalMs);
    return () => clearInterval(id);
  }, [router, intervalMs]);

  return null;
}
EOF
w src/app/orders/[id]/page.tsx <<'EOF'
import { notFound } from "next/navigation";
import { OrderStatusPoller } from "@/components/OrderStatusPoller";
import { OrderSummary } from "@/components/OrderSummary";
import { OrderViewTracker } from "@/components/OrderViewTracker";
import { RefundForm } from "@/components/RefundForm";
import { requireStaff } from "@/lib/auth";
import { orderTotal } from "@/lib/money";
import { getOrder } from "@/lib/orders";

export default async function OrderPage({ params }: { params: Promise<{ id: string }> }) {
  await requireStaff();
  const { id } = await params;
  const order = await getOrder(id);
  if (!order) notFound();
  return (
    <main>
      <OrderViewTracker orderId={order.id} plan="growth" />
      {order.status === "pending" && <OrderStatusPoller intervalMs={10_000} />}
      <article>
        <h1>{order.number}</h1>
        <p>
          {order.customerName} · {order.customerEmail}
        </p>
        <OrderSummary lines={order.lines} refundedCents={order.refundedCents} currency={order.currency} />
      </article>
      <RefundForm
        orderId={order.id}
        currency={order.currency}
        refundableCents={orderTotal(order.lines) - order.refundedCents}
      />
    </main>
  );
}
EOF
commit 30 "feat: order detail page with totals"

w src/app/orders/[id]/actions.ts <<'EOF'
"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { requireStaff } from "@/lib/auth";
import { orderTotal } from "@/lib/money";
import { getOrder, recordRefund } from "@/lib/orders";
import { refundPayment } from "@/lib/payments";

export type RefundState =
  | { status: "idle" }
  | { status: "refunded"; cents: number }
  | { status: "error"; message: string };

const refundInput = z.object({
  orderId: z.uuid(),
  amount: z.coerce.number().positive(),
});

export async function refundOrder(_previous: RefundState, formData: FormData): Promise<RefundState> {
  await requireStaff();
  const parsed = refundInput.safeParse({ orderId: formData.get("orderId"), amount: formData.get("amount") });
  if (!parsed.success) return { status: "error", message: "Enter an amount greater than zero." };
  const order = await getOrder(parsed.data.orderId);
  if (!order) return { status: "error", message: "This order no longer exists." };
  const cents = Math.round(parsed.data.amount * 100);
  if (cents > orderTotal(order.lines) - order.refundedCents) {
    return { status: "error", message: "That is more than the refundable amount." };
  }
  await refundPayment(order.paymentIntentId, cents);
  await recordRefund(order.id, cents);
  revalidatePath(`/orders/${order.id}`);
  return { status: "refunded", cents };
}
EOF
w src/components/RefundForm.tsx <<'EOF'
"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { refundOrder, type RefundState } from "@/app/orders/[id]/actions";
import { formatAmount } from "@/lib/money";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";

const initialState: RefundState = { status: "idle" };

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" disabled={pending}>
      {pending ? "Refunding…" : "Refund"}
    </Button>
  );
}

type RefundFormProps = {
  orderId: string;
  currency: string;
  refundableCents: number;
};

export function RefundForm({ orderId, currency, refundableCents }: RefundFormProps) {
  const [state, formAction] = useActionState(refundOrder, initialState);

  return (
    <form action={formAction}>
      <h2>Refund</h2>
      <p>Refundable: {formatAmount(refundableCents, currency)}</p>
      <input type="hidden" name="orderId" value={orderId} />
      <Input id="refund-amount" name="amount" label="Amount" inputMode="decimal" required />
      <SubmitButton />
      {state.status === "error" && <p role="alert">{state.message}</p>}
      {state.status === "refunded" && <p role="status">Refunded {formatAmount(state.cents, currency)}.</p>}
    </form>
  );
}
EOF
commit 21 "feat: refunds from the order page"

perl -0pi -e 's/<td>\{order.status\}<\/td>/<td>{order.status.replace("_", " ")}<\/td>/' src/components/OrdersTable.tsx
commit 6 "fix: readable order statuses"

perl -0pi -e 's/<h1>Orders<\/h1>/<h1>Orders ({orders.length})<\/h1>/' src/app/orders/page.tsx
commit 2 "feat: show how many orders match"
