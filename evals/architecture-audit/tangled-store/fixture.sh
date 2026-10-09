#!/usr/bin/env bash
# Builds a small Next.js storefront with planted structural problems.
set -euo pipefail
export GIT_AUTHOR_NAME=Fixture GIT_AUTHOR_EMAIL=fixture@example.com GIT_COMMITTER_NAME=Fixture GIT_COMMITTER_EMAIL=fixture@example.com
git init -q
w() { mkdir -p "$(dirname "$1")"; cat > "$1"; }
commit() { local days="$1"; shift; local when; when="$(date -u -v-"${days}"d +%Y-%m-%dT12:00:00Z 2>/dev/null || date -u -d "-${days} days" +%Y-%m-%dT12:00:00Z)"; git add -A; GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" git commit -qm "$*"; }

w package.json <<'EOF'
{
  "name": "storefront",
  "private": true,
  "scripts": { "dev": "next dev", "build": "next build", "lint": "next lint", "test": "vitest" },
  "dependencies": {
    "@reduxjs/toolkit": "2.2.0", "@tanstack/react-query": "5.40.0", "axios": "1.7.2",
    "next": "14.2.4", "react": "18.3.1", "react-dom": "18.3.1", "react-redux": "9.1.2", "zustand": "4.5.2"
  },
  "devDependencies": { "typescript": "5.5.2", "vitest": "1.6.0" }
}
EOF
w tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "strict": true,
    "jsx": "preserve",
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  }
}
EOF
w src/lib/http.ts <<'EOF'
import axios from 'axios';

export const http = axios.create({ baseURL: '/api', timeout: 5000 });
EOF
w src/lib/apiClient.ts <<'EOF'
export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json() as Promise<T>;
}
EOF
w src/types/api.ts <<'EOF'
export type ApiOrderResponse = {
  order_id: string;
  line_items: { sku: string; qty: number; unit_price_cents: number }[];
  customer_ref: string;
  created_at: string;
};
EOF
w src/queries/useOrders.ts <<'EOF'
import { useQuery } from '@tanstack/react-query';
import { http } from '@/lib/http';
import type { ApiOrderResponse } from '@/types/api';

export function useOrders() {
  return useQuery({
    queryKey: ['orders'],
    queryFn: async () => (await http.get<ApiOrderResponse[]>('/orders')).data,
  });
}
EOF
w src/features/cart/index.ts <<'EOF'
export { CartView } from './CartView';
export { cartTotal } from './cartTotal';
EOF
w src/features/cart/cartTotal.ts <<'EOF'
import { priceWithTax } from '@/features/checkout/checkout';

export function cartTotal(items: { price: number; quantity: number }[]): number {
  return items.reduce((sum, item) => sum + priceWithTax(item.price) * item.quantity, 0);
}
EOF
w src/features/cart/CartView.tsx <<'EOF'
'use client';
import { useEffect, useState } from 'react';
import { formatMoney } from '@/features/checkout/internal/format';
import { cartTotal } from './cartTotal';

type Item = { sku: string; price: number; quantity: number };

export function CartView() {
  const [items, setItems] = useState<Item[]>([]);
  useEffect(() => {
    const saved = window.localStorage.getItem('cart');
    setItems(saved ? JSON.parse(saved) : []);
  }, []);
  function remove(sku: string) {
    const next = items.filter((item) => item.sku !== sku);
    setItems(next);
    window.localStorage.setItem('cart', JSON.stringify(next));
  }
  return (
    <section>
      {items.map((item) => (
        <button key={item.sku} onClick={() => remove(item.sku)}>{item.sku}</button>
      ))}
      <p>{formatMoney(cartTotal(items))}</p>
    </section>
  );
}
EOF
w src/features/checkout/internal/format.ts <<'EOF'
export function formatMoney(value: number): string {
  return `£${value.toFixed(2)}`;
}
EOF
w src/features/checkout/checkout.ts <<'EOF'
import { cartTotal } from '@/features/cart';

export const TAX_RATE = 0.2;

export function priceWithTax(price: number): number {
  return price * (1 + TAX_RATE);
}

export function checkoutTotal(items: { price: number; quantity: number }[]): number {
  return cartTotal(items);
}
EOF
w src/stores/cartStore.ts <<'EOF'
import { create } from 'zustand';

type CartState = { skus: string[]; add: (sku: string) => void };

export const useCartStore = create<CartState>((set) => ({
  skus: [],
  add: (sku) =>
    set((state) => {
      const skus = [...state.skus, sku];
      window.localStorage.setItem('cart', JSON.stringify(skus.map((value) => ({ sku: value, price: 0, quantity: 1 }))));
      return { skus };
    }),
}));
EOF
w src/stores/cartSlice.ts <<'EOF'
import { createSlice, type PayloadAction } from '@reduxjs/toolkit';

const cartSlice = createSlice({
  name: 'cart',
  initialState: { lines: [] as { sku: string; quantity: number }[] },
  reducers: {
    added(state, action: PayloadAction<string>) {
      state.lines.push({ sku: action.payload, quantity: 1 });
      localStorage.setItem('cart-lines', JSON.stringify(state.lines));
    },
  },
});

export const { added } = cartSlice.actions;
export default cartSlice.reducer;
EOF
w src/components/OrderHistory.tsx <<'EOF'
'use client';
import { useEffect, useState } from 'react';
import { apiGet } from '@/lib/apiClient';
import type { ApiOrderResponse } from '@/types/api';

export function OrderHistory() {
  const [orders, setOrders] = useState<ApiOrderResponse[]>([]);
  useEffect(() => {
    apiGet<ApiOrderResponse[]>('/orders').then(setOrders);
  }, []);
  return (
    <ul>
      {orders.map((order) => (
        <li key={order.order_id}>
          {order.customer_ref}: {order.line_items.length} items, {new Date(order.created_at).toDateString()}
        </li>
      ))}
    </ul>
  );
}
EOF
w src/components/RecentOrders.tsx <<'EOF'
'use client';
import { useOrders } from '@/queries/useOrders';

export function RecentOrders() {
  const { data = [] } = useOrders();
  return <ol>{data.slice(0, 3).map((order) => <li key={order.order_id}>{order.order_id}</li>)}</ol>;
}
EOF
w src/stores/store.ts <<'EOF'
import { configureStore } from '@reduxjs/toolkit';
import cart from './cartSlice';

export const store = configureStore({ reducer: { cart } });
EOF
w src/app/providers.tsx <<'EOF'
'use client';
import { Provider } from 'react-redux';
import { store } from '@/stores/store';

export function Providers({ children }: { children: React.ReactNode }) {
  return <Provider store={store}>{children}</Provider>;
}
EOF
w src/app/layout.tsx <<'EOF'
import { Providers } from './providers';

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
EOF
w src/components/AddToCart.tsx <<'EOF'
'use client';
import { useCartStore } from '@/stores/cartStore';

export function AddToCart({ sku }: { sku: string }) {
  const add = useCartStore((state) => state.add);
  return <button onClick={() => add(sku)}>Add to cart</button>;
}
EOF
w src/components/QuickAdd.tsx <<'EOF'
'use client';
import { useDispatch } from 'react-redux';
import { added } from '@/stores/cartSlice';

export function QuickAdd({ sku }: { sku: string }) {
  const dispatch = useDispatch();
  return <button onClick={() => dispatch(added(sku))}>Quick add</button>;
}
EOF
w src/app/page.tsx <<'EOF'
import { CartView } from '@/features/cart';
import { AddToCart } from '@/components/AddToCart';
import { OrderHistory } from '@/components/OrderHistory';
import { QuickAdd } from '@/components/QuickAdd';
import { RecentOrders } from '@/components/RecentOrders';

export default function Home() {
  return (
    <main>
      <AddToCart sku="tea-001" />
      <QuickAdd sku="mug-002" />
      <CartView />
      <RecentOrders />
      <OrderHistory />
    </main>
  );
}
EOF
w src/legacy/oldCart.ts <<'EOF'
export function legacyCartCount(raw: string | null): number {
  return raw ? JSON.parse(raw).length : 0;
}
EOF
commit 120 "feat: storefront with cart, checkout, and orders"

for n in 1 2 3 4 5 6 7 8 9 10; do
  cat >> src/features/checkout/checkout.ts <<EOF

export function discountTier$n(total: number): number {
  return total > $((n * 50)) ? total * 0.0$n : 0;
}
EOF
  cat >> src/features/cart/CartView.tsx <<EOF

export const CART_VERSION_$n = $n;
EOF
  commit $((100 - n * 8)) "feat: checkout discount tier $n"
done
