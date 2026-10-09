#!/usr/bin/env bash
# Builds a small Next.js App Router invoicing app with planted security problems.
set -euo pipefail
export GIT_AUTHOR_NAME=Fixture GIT_AUTHOR_EMAIL=fixture@example.com GIT_COMMITTER_NAME=Fixture GIT_COMMITTER_EMAIL=fixture@example.com
git init -q
# A personal or global ignore file must not change what the audit sees.
git config core.excludesFile /dev/null
w() { mkdir -p "$(dirname "$1")"; cat > "$1"; }
commit() { local days="$1"; shift; local when; when="$(date -u -v-"${days}"d +%Y-%m-%dT12:00:00Z 2>/dev/null || date -u -d "-${days} days" +%Y-%m-%dT12:00:00Z)"; git add -A; GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" git commit -qm "$*"; }

w package.json <<'EOF'
{
  "name": "invoicer",
  "private": true,
  "scripts": { "dev": "next dev", "build": "next build", "start": "next start", "lint": "next lint" },
  "dependencies": {
    "@ai-sdk/openai": "1.3.22",
    "@ai-sdk/react": "1.2.12",
    "@prisma/client": "6.8.2",
    "ai": "4.3.16",
    "bcryptjs": "3.0.2",
    "jose": "6.0.11",
    "marked": "15.0.12",
    "next": "15.1.6",
    "react": "19.0.0",
    "react-dom": "19.0.0",
    "stripe": "16.12.0",
    "zod": "3.25.28"
  },
  "devDependencies": { "@types/node": "22.15.21", "@types/react": "19.0.10", "prisma": "6.8.2", "typescript": "5.8.3" }
}
EOF
w tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "strict": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "jsx": "preserve",
    "noEmit": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
  "exclude": ["node_modules"]
}
EOF
w .gitignore <<'EOF'
node_modules
.next
EOF
w .env <<'EOF'
NEXT_PUBLIC_APP_URL=http://localhost:3000
EOF
w next.config.ts <<'EOF'
import type { NextConfig } from 'next';

const nextConfig: NextConfig = {};

export default nextConfig;
EOF
w prisma/schema.prisma <<'EOF'
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model User {
  id           String    @id @default(uuid())
  email        String    @unique
  passwordHash String
  invoices     Invoice[]
}

model Invoice {
  id              String   @id @default(uuid())
  number          String
  ownerId         String
  owner           User     @relation(fields: [ownerId], references: [id])
  amountCents     Int
  currency        String   @default("GBP")
  status          String   @default("open")
  customerEmail   String
  customerNote    String?
  paymentIntentId String?
  createdAt       DateTime @default(now())
}
EOF
w lib/db.ts <<'EOF'
import { PrismaClient } from '@prisma/client';

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const db = globalForPrisma.prisma ?? new PrismaClient();

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = db;
EOF
w lib/token.ts <<'EOF'
import { SignJWT, jwtVerify } from 'jose';

const key = new TextEncoder().encode(process.env.SESSION_SECRET);

export type Session = { userId: string };

export async function createSessionToken(userId: string): Promise<string> {
  return new SignJWT({ userId }).setProtectedHeader({ alg: 'HS256' }).setIssuedAt().setExpirationTime('7d').sign(key);
}

export async function verifySessionToken(token: string | undefined): Promise<Session | null> {
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, key, { algorithms: ['HS256'] });
    return { userId: String(payload.userId) };
  } catch {
    return null;
  }
}
EOF
w lib/session.ts <<'EOF'
import { cookies } from 'next/headers';
import { verifySessionToken } from './token';

export async function getSession() {
  return verifySessionToken((await cookies()).get('session')?.value);
}
EOF
stripe_key="sk_""live_""51Hq""FixtureOnly""NotARealKey""0000"
printf '%s\n' \
  "import Stripe from 'stripe';" \
  "" \
  "export const stripe = new Stripe('${stripe_key}', { apiVersion: '2024-06-20' });" > lib/payments.ts
w lib/apiClient.ts <<'EOF'
export type InvoiceSummary = { id: string; number: string; amountCents: number; currency: string; status: string };

export async function fetchInvoice(id: string): Promise<InvoiceSummary> {
  const response = await fetch(`/api/invoices/${id}`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('accessToken')}` },
  });
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}
EOF
w middleware.ts <<'EOF'
import { NextResponse, type NextRequest } from 'next/server';
import { verifySessionToken } from '@/lib/token';

export async function middleware(request: NextRequest) {
  const session = await verifySessionToken(request.cookies.get('session')?.value);
  if (!session) {
    const login = new URL('/login', request.url);
    login.searchParams.set('next', request.nextUrl.pathname);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = { matcher: ['/dashboard/:path*'] };
EOF
w app/layout.tsx <<'EOF'
export const metadata = { title: 'Invoicer' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
EOF
w app/page.tsx <<'EOF'
import Link from 'next/link';

export default function Home() {
  return (
    <main>
      <h1>Invoicer</h1>
      <Link href="/login">Sign in</Link>
    </main>
  );
}
EOF
w app/login/page.tsx <<'EOF'
import { Suspense } from 'react';
import { LoginForm } from './LoginForm';

export default function LoginPage() {
  return (
    <main>
      <h1>Sign in</h1>
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
EOF
w app/login/actions.ts <<'EOF'
'use server';

import bcrypt from 'bcryptjs';
import { cookies } from 'next/headers';
import { db } from '@/lib/db';
import { createSessionToken } from '@/lib/token';

export async function login(formData: FormData) {
  const email = String(formData.get('email'));
  const password = String(formData.get('password'));
  const user = await db.user.findUnique({ where: { email } });
  if (!user || !(await bcrypt.compare(password, user.passwordHash))) {
    return { error: 'Invalid email or password' };
  }
  const token = await createSessionToken(user.id);
  (await cookies()).set('session', token);
  return { token };
}
EOF
w app/login/LoginForm.tsx <<'EOF'
'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';
import { login } from './actions';

export function LoginForm() {
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  async function submit(formData: FormData) {
    const result = await login(formData);
    if ('error' in result) {
      setError(result.error);
      return;
    }
    localStorage.setItem('accessToken', result.token);
    window.location.assign(searchParams.get('next') ?? '/dashboard');
  }

  return (
    <form action={submit}>
      <input name="email" type="email" required />
      <input name="password" type="password" required />
      <button type="submit">Sign in</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
EOF
w app/dashboard/page.tsx <<'EOF'
import Link from 'next/link';
import { db } from '@/lib/db';
import { getSession } from '@/lib/session';
import { QuickView } from './QuickView';

export default async function Dashboard() {
  const session = await getSession();
  const invoices = await db.invoice.findMany({ where: { ownerId: session!.userId }, orderBy: { createdAt: 'desc' } });
  return (
    <main>
      <h1>Invoices</h1>
      <ul>
        {invoices.map((invoice) => (
          <li key={invoice.id}>
            <Link href={`/dashboard/invoices/${invoice.id}`}>{invoice.number}</Link> — {invoice.status}
          </li>
        ))}
      </ul>
      <QuickView />
      <Link href="/dashboard/assistant">Ask the billing assistant</Link>
    </main>
  );
}
EOF
w app/dashboard/QuickView.tsx <<'EOF'
'use client';

import { useState } from 'react';
import { fetchInvoice, type InvoiceSummary } from '@/lib/apiClient';

export function QuickView() {
  const [id, setId] = useState('');
  const [invoice, setInvoice] = useState<InvoiceSummary | null>(null);
  return (
    <section>
      <input value={id} onChange={(event) => setId(event.target.value)} placeholder="Invoice id" />
      <button type="button" onClick={async () => setInvoice(await fetchInvoice(id))}>Open</button>
      {invoice && <p>{invoice.number}: {(invoice.amountCents / 100).toFixed(2)} {invoice.currency} ({invoice.status})</p>}
    </section>
  );
}
EOF
w app/dashboard/invoices/actions.ts <<'EOF'
'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/db';

export async function deleteInvoice(invoiceId: string) {
  await db.invoice.delete({ where: { id: invoiceId } });
  revalidatePath('/dashboard');
}
EOF
w 'app/dashboard/invoices/[id]/page.tsx' <<'EOF'
import { marked } from 'marked';
import { notFound } from 'next/navigation';
import { db } from '@/lib/db';
import { DeleteButton } from './DeleteButton';
import { WalkthroughPreview } from './WalkthroughPreview';

export default async function InvoicePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const invoice = await db.invoice.findUnique({ where: { id } });
  if (!invoice) notFound();
  const note = await marked.parse(invoice.customerNote ?? '');
  return (
    <article>
      <h1>Invoice {invoice.number}</h1>
      <p>
        {(invoice.amountCents / 100).toFixed(2)} {invoice.currency} — {invoice.status}
      </p>
      <h2>Message from {invoice.customerEmail}</h2>
      <div dangerouslySetInnerHTML={{ __html: note }} />
      <WalkthroughPreview />
      <DeleteButton invoiceId={invoice.id} />
    </article>
  );
}
EOF
w 'app/dashboard/invoices/[id]/DeleteButton.tsx' <<'EOF'
'use client';

import { useTransition } from 'react';
import { deleteInvoice } from '../actions';

export function DeleteButton({ invoiceId }: { invoiceId: string }) {
  const [pending, startTransition] = useTransition();
  return (
    <button type="button" disabled={pending} onClick={() => startTransition(() => deleteInvoice(invoiceId))}>
      Delete invoice
    </button>
  );
}
EOF
w 'app/dashboard/invoices/[id]/WalkthroughPreview.tsx' <<'EOF'
'use client';

import { useState } from 'react';

export function WalkthroughPreview() {
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState<string | null>(null);

  async function preview() {
    const response = await fetch(`/api/embed?url=${encodeURIComponent(url)}`);
    setTitle(response.ok ? ((await response.json()) as { title: string | null }).title : null);
  }

  return (
    <section>
      <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="Walkthrough video link" />
      <button type="button" onClick={preview}>Preview</button>
      {title && <p>{title}</p>}
    </section>
  );
}
EOF
w 'app/api/invoices/[id]/route.ts' <<'EOF'
import { NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { verifySessionToken } from '@/lib/token';

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const token = request.headers.get('authorization')?.replace('Bearer ', '');
  const session = await verifySessionToken(token);
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const { id } = await params;
  const invoice = await db.invoice.findUnique({ where: { id } });
  if (!invoice) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  return NextResponse.json(invoice);
}
EOF
w app/api/embed/route.ts <<'EOF'
export async function GET(request: Request) {
  const target = new URL(request.url).searchParams.get('url');
  if (!target) return Response.json({ error: 'Missing url' }, { status: 400 });
  const response = await fetch(target);
  const html = await response.text();
  const title = html.match(/<title>([^<]*)<\/title>/i)?.[1] ?? null;
  return Response.json({ title, status: response.status, preview: html.slice(0, 2000) });
}
EOF
w 'app/pay/[id]/page.tsx' <<'EOF'
import { notFound } from 'next/navigation';
import { db } from '@/lib/db';
import { addCustomerNote } from './actions';

// Customers reach this page from the payment link in their invoice email.
export default async function PayPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const invoice = await db.invoice.findUnique({
    where: { id },
    select: { id: true, number: true, amountCents: true, currency: true, status: true },
  });
  if (!invoice) notFound();
  return (
    <main>
      <h1>Invoice {invoice.number}</h1>
      <p>
        Amount due: {(invoice.amountCents / 100).toFixed(2)} {invoice.currency}
      </p>
      <form action={addCustomerNote}>
        <input type="hidden" name="invoiceId" value={invoice.id} />
        <textarea name="note" placeholder="Add a message for the merchant (Markdown supported)" />
        <button type="submit">Send</button>
      </form>
    </main>
  );
}
EOF
w 'app/pay/[id]/actions.ts' <<'EOF'
'use server';

import { db } from '@/lib/db';

export async function addCustomerNote(formData: FormData) {
  await db.invoice.update({
    where: { id: String(formData.get('invoiceId')) },
    data: { customerNote: String(formData.get('note')) },
  });
}
EOF
commit 60 "feat: invoices, payment page, and sign-in"

w app/api/assistant/route.ts <<'EOF'
import { openai } from '@ai-sdk/openai';
import { streamText, tool } from 'ai';
import { z } from 'zod';
import { db } from '@/lib/db';
import { stripe } from '@/lib/payments';
import { getSession } from '@/lib/session';

export async function POST(request: Request) {
  const session = await getSession();
  if (!session) return new Response('Unauthorized', { status: 401 });
  const { messages } = await request.json();

  const result = streamText({
    model: openai('gpt-4o-mini'),
    system: 'You are the Invoicer billing assistant. Help the user with their invoices and refunds.',
    messages,
    maxSteps: 5,
    tools: {
      lookupInvoice: tool({
        description: 'Look up an invoice, including the customer message, by its id',
        parameters: z.object({ invoiceId: z.string() }),
        execute: async ({ invoiceId }) => db.invoice.findUnique({ where: { id: invoiceId } }),
      }),
      refundInvoice: tool({
        description: 'Refund a paid invoice in full',
        parameters: z.object({ invoiceId: z.string() }),
        execute: async ({ invoiceId }) => {
          const invoice = await db.invoice.findUniqueOrThrow({ where: { id: invoiceId } });
          await stripe.refunds.create({ payment_intent: invoice.paymentIntentId! });
          await db.invoice.update({ where: { id: invoiceId }, data: { status: 'refunded' } });
          return { refunded: invoiceId };
        },
      }),
    },
  });
  return result.toDataStreamResponse();
}
EOF
w app/dashboard/assistant/page.tsx <<'EOF'
import { Assistant } from './Assistant';

export default function AssistantPage() {
  return (
    <main>
      <h1>Billing assistant</h1>
      <Assistant />
    </main>
  );
}
EOF
w app/dashboard/assistant/Assistant.tsx <<'EOF'
'use client';

import { useChat } from '@ai-sdk/react';

export function Assistant() {
  const { messages, input, handleInputChange, handleSubmit, status } = useChat({ api: '/api/assistant' });
  return (
    <section>
      {messages.map((message) => (
        <p key={message.id} style={{ whiteSpace: 'pre-wrap' }}>
          <strong>{message.role === 'user' ? 'You' : 'Assistant'}:</strong> {message.content}
        </p>
      ))}
      <form onSubmit={handleSubmit}>
        <input value={input} onChange={handleInputChange} placeholder="Ask about an invoice" />
        <button type="submit" disabled={status !== 'ready'}>Send</button>
      </form>
    </section>
  );
}
EOF
commit 20 "feat: billing assistant with invoice lookup and refunds"
