import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CRM Pulse — Semantic BI",
  description: "Умный дашборд CRM с адаптивными сценариями и метриками GraphDB",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
