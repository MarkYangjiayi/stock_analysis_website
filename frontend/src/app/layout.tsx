import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import TopNavBar from "@/components/TopNavBar";
import StatusBar from "@/components/shell/StatusBar";
import { ThemeProvider } from "@/components/ThemeProvider";

export const metadata: Metadata = {
  title: "Quantify — Low Frequency Quant Research",
  description: "Point-in-time equity research, factor analysis and portfolio backtesting.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <body
        className="flex h-screen w-full flex-col overflow-hidden antialiased"
      >
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <a
            href="#main-content"
            className="sr-only z-[100] rounded-md bg-accent px-4 py-2 text-on-accent focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
          >
            Skip to main content
          </a>
          <TopNavBar />
          <main id="main-content" className="min-h-0 w-full flex-1 overflow-hidden">
            {children}
          </main>
          <StatusBar />
        </ThemeProvider>
      </body>
    </html>
  );
}
