import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BET ANALIZADOR | Inteligencia deportiva",
  description: "Analisis futbolistico basado en datos, contexto y modelos versionados.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
