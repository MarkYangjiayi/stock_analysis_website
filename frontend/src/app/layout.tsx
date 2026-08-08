import type { Metadata } from "next";
import "./globals.css";
import TopNavBar from "@/components/TopNavBar";
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
    <html lang="en" suppressHydrationWarning>
      <body
        className="flex h-screen w-full flex-col overflow-hidden antialiased transition-colors duration-300"
      >
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <a
            href="#main-content"
            className="sr-only z-[100] rounded-lg bg-emerald-600 px-4 py-2 text-white focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
          >
            Skip to main content
          </a>
          <TopNavBar />
          <main id="main-content" className="min-h-0 w-full flex-1 overflow-hidden">
            {children}
          </main>
        </ThemeProvider>
      </body>
    </html>
  );
}
