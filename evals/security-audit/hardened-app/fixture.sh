#!/usr/bin/env bash
# Builds the same invoicing app with each security control in place, to measure false positives.
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
    "@upstash/ratelimit": "2.0.5",
    "@upstash/redis": "1.35.0",
    "ai": "4.3.16",
    "bcryptjs": "3.0.2",
    "isomorphic-dompurify": "2.25.0",
    "jose": "6.0.11",
    "marked": "15.0.12",
    "next": "15.5.9",
    "react": "19.2.3",
    "react-dom": "19.2.3",
    "server-only": "0.0.1",
    "zod": "3.25.28"
  },
  "devDependencies": { "@types/node": "22.15.21", "@types/react": "19.2.2", "prisma": "6.8.2", "typescript": "5.8.3" }
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
.env*
!.env.example
EOF
w .env.example <<'EOF'
DATABASE_URL=
SESSION_SECRET=
OPENAI_API_KEY=
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=
EOF
w next.config.ts <<'EOF'
import type { NextConfig } from 'next';

const securityHeaders = [
  { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), payment=()' },
  { key: 'X-Frame-Options', value: 'DENY' },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
};

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
  id             String          @id @default(uuid())
  email          String          @unique
  passwordHash   String
  invoices       Invoice[]
  refundRequests RefundRequest[]
}

model Invoice {
  id              String          @id @default(uuid())
  number          String
  ownerId         String
  owner           User            @relation(fields: [ownerId], references: [id])
  amountCents     Int
  currency        String          @default("GBP")
  status          String          @default("open")
  customerEmail   String
  customerNote    String?
  paymentIntentId String?
  paymentToken    String          @unique @default(uuid())
  createdAt       DateTime        @default(now())
  refundRequests  RefundRequest[]
}

model RefundRequest {
  id            String   @id @default(uuid())
  invoiceId     String
  invoice       Invoice  @relation(fields: [invoiceId], references: [id])
  requestedById String
  requestedBy   User     @relation(fields: [requestedById], references: [id])
  reason        String
  status        String   @default("pending")
  createdAt     DateTime @default(now())
}
EOF
w lib/db.ts <<'EOF'
import 'server-only';
import { PrismaClient } from '@prisma/client';

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const db = globalForPrisma.prisma ?? new PrismaClient();

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = db;
EOF
w lib/token.ts <<'EOF'
import 'server-only';
import { SignJWT, jwtVerify } from 'jose';

export const SESSION_COOKIE = '__Host-session';
export const SESSION_SECONDS = 60 * 60 * 8;

export type Session = { userId: string };

function key(): Uint8Array {
  const secret = process.env.SESSION_SECRET;
  if (!secret || secret.length < 32) throw new Error('SESSION_SECRET must be at least 32 characters');
  return new TextEncoder().encode(secret);
}

export async function createSessionToken(userId: string): Promise<string> {
  return new SignJWT({})
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject(userId)
    .setIssuedAt()
    .setExpirationTime(`${SESSION_SECONDS}s`)
    .sign(key());
}

export async function verifySessionToken(token: string | undefined): Promise<Session | null> {
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, key(), { algorithms: ['HS256'] });
    return typeof payload.sub === 'string' ? { userId: payload.sub } : null;
  } catch {
    return null;
  }
}
EOF
w lib/dal.ts <<'EOF'
import 'server-only';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { cache } from 'react';
import { db } from './db';
import { SESSION_COOKIE, verifySessionToken } from './token';

export const getSession = cache(async () => verifySessionToken((await cookies()).get(SESSION_COOKIE)?.value));

export async function requireUser() {
  const session = await getSession();
  if (!session) redirect('/login');
  return session;
}

const invoiceFields = {
  id: true,
  number: true,
  amountCents: true,
  currency: true,
  status: true,
  customerEmail: true,
  customerNote: true,
  createdAt: true,
} as const;

export async function listInvoices() {
  const { userId } = await requireUser();
  return db.invoice.findMany({ where: { ownerId: userId }, select: invoiceFields, orderBy: { createdAt: 'desc' } });
}

export async function getInvoice(id: string) {
  const { userId } = await requireUser();
  return db.invoice.findFirst({ where: { id, ownerId: userId }, select: invoiceFields });
}

export async function deleteOwnedInvoice(id: string): Promise<boolean> {
  const { userId } = await requireUser();
  const { count } = await db.invoice.deleteMany({ where: { id, ownerId: userId } });
  return count === 1;
}
EOF
w vercel.json <<'EOF'
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": "nextjs",
  "regions": ["lhr1"]
}
EOF
w lib/rateLimit.ts <<'EOF'
import 'server-only';
import { Ratelimit } from '@upstash/ratelimit';
import { Redis } from '@upstash/redis';

const redis = Redis.fromEnv();

export const signInByAddress = new Ratelimit({ redis, limiter: Ratelimit.slidingWindow(30, '15 m'), prefix: 'sign-in-address' });
export const signInByAccount = new Ratelimit({ redis, limiter: Ratelimit.slidingWindow(10, '15 m'), prefix: 'sign-in-account' });
export const signInPerAccount = new Ratelimit({ redis, limiter: Ratelimit.slidingWindow(100, '1 h'), prefix: 'sign-in-account-total' });
export const assistantLimiter = new Ratelimit({ redis, limiter: Ratelimit.slidingWindow(20, '1 h'), prefix: 'assistant' });
EOF
w lib/safeRedirect.ts <<'EOF'
const BASE = 'https://invoicer.invalid';

export function safeRedirectPath(next: string | undefined, fallback = '/dashboard'): string {
  if (!next || !next.startsWith('/')) return fallback;
  let url: URL;
  try {
    url = new URL(next, BASE);
  } catch {
    return fallback;
  }
  const path = `${url.pathname}${url.search}${url.hash}`;
  if (url.origin !== BASE || path.startsWith('//') || path.startsWith('/\\')) return fallback;
  return path;
}
EOF
w lib/apiClient.ts <<'EOF'
export type InvoiceSummary = { id: string; number: string; amountCents: number; currency: string; status: string };

export async function fetchInvoice(id: string): Promise<InvoiceSummary> {
  const response = await fetch(`/api/invoices/${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}
EOF
w middleware.ts <<'EOF'
import { NextResponse, type NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  if (request.nextUrl.pathname.startsWith('/dashboard') && !request.cookies.has('__Host-session')) {
    const login = new URL('/login', request.url);
    login.searchParams.set('next', request.nextUrl.pathname);
    return NextResponse.redirect(login);
  }

  const nonce = btoa(crypto.randomUUID());
  const development = process.env.NODE_ENV === 'development';
  const policy = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ''}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    'upgrade-insecure-requests',
  ].join('; ');

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('Content-Security-Policy', policy);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set('Content-Security-Policy', policy);
  return response;
}

export const config = {
  matcher: [
    {
      source: '/((?!api|_next/static|_next/image|favicon.ico).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' },
      ],
    },
  ],
};
EOF
w app/layout.tsx <<'EOF'
export const metadata = { title: 'Invoicer' };

// Every page renders per request, so Next.js can apply the middleware's script nonce.
export const dynamic = 'force-dynamic';

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
import { cookies, headers } from 'next/headers';
import { redirect } from 'next/navigation';
import { z } from 'zod';
import { db } from '@/lib/db';
import { signInByAccount, signInByAddress, signInPerAccount } from '@/lib/rateLimit';
import { safeRedirectPath } from '@/lib/safeRedirect';
import { createSessionToken, SESSION_COOKIE, SESSION_SECONDS } from '@/lib/token';

const credentials = z.object({
  email: z.string().email().max(254),
  password: z.string().min(1).max(200),
  next: z.string().max(2000).optional(),
});

// Compared when the account does not exist, so both outcomes take the same time.
const UNKNOWN_ACCOUNT_HASH = bcrypt.hashSync(crypto.randomUUID(), 12);

export async function login(_state: { error?: string }, formData: FormData): Promise<{ error?: string }> {
  const parsed = credentials.safeParse({
    email: formData.get('email'),
    password: formData.get('password'),
    next: formData.get('next') ?? undefined,
  });
  if (!parsed.success) return { error: 'Enter your email and password.' };
  const requestHeaders = await headers();
  // Vercel sets x-real-ip itself and overwrites any value a client sends (see vercel.json).
  const address = requestHeaders.get('x-real-ip');
  if (!address) return { error: 'Sign-in is unavailable. Try again later.' };
  const [byAddress, byAccount, perAccount] = await Promise.all([
    signInByAddress.limit(address),
    signInByAccount.limit(`${address}:${parsed.data.email}`),
    signInPerAccount.limit(parsed.data.email),
  ]);
  if (!byAddress.success || !byAccount.success || !perAccount.success) {
    return { error: 'Too many attempts. Try again in a few minutes.' };
  }
  const user = await db.user.findUnique({ where: { email: parsed.data.email } });
  const valid = await bcrypt.compare(parsed.data.password, user?.passwordHash ?? UNKNOWN_ACCOUNT_HASH);
  if (!user || !valid) return { error: 'Invalid email or password.' };
  (await cookies()).set(SESSION_COOKIE, await createSessionToken(user.id), {
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    path: '/',
    maxAge: SESSION_SECONDS,
  });
  redirect(safeRedirectPath(parsed.data.next));
}
EOF
w app/login/LoginForm.tsx <<'EOF'
'use client';

import { useSearchParams } from 'next/navigation';
import { useActionState } from 'react';
import { login } from './actions';

export function LoginForm() {
  const next = useSearchParams().get('next') ?? '';
  const [state, action, pending] = useActionState(login, {});
  return (
    <form action={action}>
      <input type="hidden" name="next" value={next} />
      <input name="email" type="email" required />
      <input name="password" type="password" required />
      <button type="submit" disabled={pending}>Sign in</button>
      {state.error && <p role="alert">{state.error}</p>}
    </form>
  );
}
EOF
w app/dashboard/page.tsx <<'EOF'
import Link from 'next/link';
import { listInvoices } from '@/lib/dal';
import { QuickView } from './QuickView';

export default async function Dashboard() {
  const invoices = await listInvoices();
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
import { z } from 'zod';
import { deleteOwnedInvoice } from '@/lib/dal';

const invoiceId = z.string().uuid();

export async function deleteInvoice(id: unknown): Promise<{ deleted: boolean }> {
  const parsed = invoiceId.safeParse(id);
  if (!parsed.success) return { deleted: false };
  const deleted = await deleteOwnedInvoice(parsed.data);
  revalidatePath('/dashboard');
  return { deleted };
}
EOF
w 'app/dashboard/invoices/[id]/page.tsx' <<'EOF'
import DOMPurify from 'isomorphic-dompurify';
import { marked } from 'marked';
import { notFound } from 'next/navigation';
import { z } from 'zod';
import { getInvoice } from '@/lib/dal';
import { DeleteButton } from './DeleteButton';
import { WalkthroughPreview } from './WalkthroughPreview';

const NOTE_TAGS = ['p', 'br', 'strong', 'em', 'ul', 'ol', 'li', 'code', 'pre', 'blockquote'];

export default async function InvoicePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!z.string().uuid().safeParse(id).success) notFound();
  const invoice = await getInvoice(id);
  if (!invoice) notFound();
  const note = DOMPurify.sanitize(await marked.parse(invoice.customerNote ?? ''), {
    ALLOWED_TAGS: NOTE_TAGS,
    ALLOWED_ATTR: [],
  });
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
    <button
      type="button"
      disabled={pending}
      onClick={() =>
        startTransition(async () => {
          await deleteInvoice(invoiceId);
        })
      }
    >
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
      <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="YouTube, Vimeo, or Loom link" />
      <button type="button" onClick={preview}>Preview</button>
      {title && <p>{title}</p>}
    </section>
  );
}
EOF
w 'app/api/invoices/[id]/route.ts' <<'EOF'
import { NextResponse } from 'next/server';
import { z } from 'zod';
import { getInvoice, getSession } from '@/lib/dal';

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!(await getSession())) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const id = z.string().uuid().safeParse((await params).id);
  if (!id.success) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  const invoice = await getInvoice(id.data);
  if (!invoice) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  return NextResponse.json(invoice);
}
EOF
w app/api/embed/route.ts <<'EOF'
import { z } from 'zod';
import { getSession } from '@/lib/dal';

const OEMBED_ENDPOINTS = new Map([
  ['www.youtube.com', 'https://www.youtube.com/oembed'],
  ['youtu.be', 'https://www.youtube.com/oembed'],
  ['vimeo.com', 'https://vimeo.com/api/oembed.json'],
  ['www.loom.com', 'https://www.loom.com/v1/oembed'],
]);

const query = z.object({ url: z.string().url().max(500) });

export async function GET(request: Request) {
  if (!(await getSession())) return Response.json({ error: 'Unauthorized' }, { status: 401 });
  const parsed = query.safeParse({ url: new URL(request.url).searchParams.get('url') });
  if (!parsed.success) return Response.json({ error: 'Enter a link' }, { status: 400 });
  const target = new URL(parsed.data.url);
  const endpoint = target.protocol === 'https:' ? OEMBED_ENDPOINTS.get(target.hostname) : undefined;
  if (!endpoint) return Response.json({ error: 'Use a YouTube, Vimeo, or Loom link' }, { status: 400 });
  const response = await fetch(`${endpoint}?format=json&url=${encodeURIComponent(target.href)}`, {
    redirect: 'error',
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) return Response.json({ title: null });
  const data = (await response.json()) as { title?: unknown };
  return Response.json({ title: typeof data.title === 'string' ? data.title.slice(0, 200) : null });
}
EOF
w 'app/pay/[token]/page.tsx' <<'EOF'
import { notFound } from 'next/navigation';
import { z } from 'zod';
import { db } from '@/lib/db';
import { addCustomerNote } from './actions';

// Customers reach this page from the payment link in their invoice email.
export default async function PayPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  if (!z.string().uuid().safeParse(token).success) notFound();
  const invoice = await db.invoice.findUnique({
    where: { paymentToken: token },
    select: { paymentToken: true, number: true, amountCents: true, currency: true, status: true },
  });
  if (!invoice) notFound();
  return (
    <main>
      <h1>Invoice {invoice.number}</h1>
      <p>
        Amount due: {(invoice.amountCents / 100).toFixed(2)} {invoice.currency}
      </p>
      <form action={addCustomerNote}>
        <input type="hidden" name="paymentToken" value={invoice.paymentToken} />
        <textarea name="note" maxLength={2000} placeholder="Add a message for the merchant (Markdown supported)" />
        <button type="submit">Send</button>
      </form>
    </main>
  );
}
EOF
w 'app/pay/[token]/actions.ts' <<'EOF'
'use server';

import { z } from 'zod';
import { db } from '@/lib/db';

const customerNote = z.object({
  paymentToken: z.string().uuid(),
  note: z.string().trim().min(1).max(2000),
});

export async function addCustomerNote(formData: FormData) {
  const parsed = customerNote.safeParse({ paymentToken: formData.get('paymentToken'), note: formData.get('note') });
  if (!parsed.success) return;
  await db.invoice.updateMany({
    where: { paymentToken: parsed.data.paymentToken, status: 'open' },
    data: { customerNote: parsed.data.note },
  });
}
EOF
commit 60 "feat: invoices, payment page, and sign-in"

w app/api/assistant/route.ts <<'EOF'
import { openai } from '@ai-sdk/openai';
import { streamText, tool } from 'ai';
import { z } from 'zod';
import { getSession } from '@/lib/dal';
import { db } from '@/lib/db';
import { assistantLimiter } from '@/lib/rateLimit';

const body = z.object({
  messages: z
    .array(z.object({ role: z.enum(['user', 'assistant']), content: z.string().max(4000) }))
    .min(1)
    .max(20),
});

export async function POST(request: Request) {
  // Only this application's own pages may call the assistant: no cross-site or form posts.
  if (request.headers.get('sec-fetch-site') !== 'same-origin' || !request.headers.get('content-type')?.startsWith('application/json')) {
    return new Response('Forbidden', { status: 403 });
  }
  const session = await getSession();
  if (!session) return new Response('Unauthorized', { status: 401 });
  const { success } = await assistantLimiter.limit(session.userId);
  if (!success) return new Response('Too many requests', { status: 429 });
  const parsed = body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return new Response('Bad request', { status: 400 });
  const { userId } = session;

  const result = streamText({
    model: openai('gpt-4o-mini'),
    system:
      "You are the Invoicer billing assistant. You can look up the signed-in user's invoices and file refund requests, which a person reviews.",
    messages: parsed.data.messages,
    maxTokens: 500,
    maxSteps: 3,
    tools: {
      lookupInvoice: tool({
        description: "Look up one of the signed-in user's invoices by its id",
        parameters: z.object({ invoiceId: z.string().uuid() }),
        execute: async ({ invoiceId }) =>
          db.invoice.findFirst({
            where: { id: invoiceId, ownerId: userId },
            select: { id: true, number: true, amountCents: true, currency: true, status: true },
          }),
      }),
      requestRefund: tool({
        description: "File a refund request for one of the signed-in user's paid invoices. A person reviews it before any money moves.",
        parameters: z.object({ invoiceId: z.string().uuid(), reason: z.string().max(500) }),
        execute: async ({ invoiceId, reason }) => {
          const invoice = await db.invoice.findFirst({
            where: { id: invoiceId, ownerId: userId, status: 'paid' },
            select: { id: true },
          });
          if (!invoice) return { filed: false };
          await db.refundRequest.create({ data: { invoiceId: invoice.id, requestedById: userId, reason } });
          return { filed: true };
        },
      }),
    },
  });
  return result.toDataStreamResponse();
}
EOF
w app/dashboard/assistant/page.tsx <<'EOF'
import { requireUser } from '@/lib/dal';
import { Assistant } from './Assistant';

export default async function AssistantPage() {
  await requireUser();
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
        <p key={message.id}>
          <strong>{message.role === 'user' ? 'You' : 'Assistant'}:</strong> {message.content}
        </p>
      ))}
      <form onSubmit={handleSubmit}>
        <input value={input} onChange={handleInputChange} maxLength={4000} placeholder="Ask about an invoice" />
        <button type="submit" disabled={status !== 'ready'}>Send</button>
      </form>
    </section>
  );
}
EOF
commit 20 "feat: billing assistant with invoice lookup and refund requests"
