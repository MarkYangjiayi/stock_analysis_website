"use client";

import React, { useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';

export default function TopNavBar() {
    const pathname = usePathname();
    const router = useRouter();
    const [searchInput, setSearchInput] = useState('');

    const handleSearch = (e: React.FormEvent) => {
        e.preventDefault();
        if (searchInput.trim()) {
            router.push(`/?ticker=${searchInput.toUpperCase().trim()}`);
            setSearchInput(''); // optional: clear after search
        }
    };

    const navLinks = [
        { name: 'Analysis', path: '/' },
        { name: 'Screener', path: '/screener' },
        { name: 'Anomalies', path: '/anomalies' },
        { name: 'Market Rotation', path: '/rrg' },
    ];

    return (
        <nav className="h-16 w-full bg-[#0B0E14] border-b border-gray-800 flex items-center justify-between px-6 sticky top-0 z-50">
            {/* Left side: Logo and Links */}
            <div className="flex items-center gap-8 h-full">
                {/* Logo */}
                <Link href="/" className="flex items-center gap-2 group">
                    <div className="w-8 h-8 rounded bg-gradient-to-br from-emerald-400 to-cyan-500 flex items-center justify-center text-black font-black text-lg shadow-[0_0_15px_rgba(16,185,129,0.4)] group-hover:scale-105 transition-transform">
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                        </svg>
                    </div>
                    <span className="text-xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-emerald-400 to-cyan-500">
                        Quantify
                    </span>
                </Link>

                {/* Navigation Links */}
                <div className="hidden md:flex items-center h-full space-x-1">
                    {navLinks.map((link) => {
                        const isActive = pathname === link.path;
                        return (
                            <Link
                                key={link.name}
                                href={link.path}
                                className={`relative px-4 h-full flex items-center text-sm font-medium transition-colors ${isActive ? 'text-white' : 'text-gray-400 hover:text-gray-200'
                                    }`}
                            >
                                {link.name}
                                {isActive && (
                                    <span className="absolute bottom-0 left-0 w-full h-[2px] bg-emerald-500 rounded-t-sm shadow-[0_-2px_8px_rgba(16,185,129,0.5)]" />
                                )}
                            </Link>
                        );
                    })}
                </div>
            </div>

            {/* Right side: Global Search */}
            <div className="flex items-center">
                <form onSubmit={handleSearch} className="relative group w-64">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                        <svg className="h-4 w-4 text-gray-400 group-focus-within:text-emerald-400 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                        </svg>
                    </div>
                    <input
                        type="text"
                        className="block w-full pl-10 pr-3 py-1.5 border border-gray-700 rounded-md leading-5 bg-[#151922] text-gray-300 placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 focus:border-emerald-500 focus:bg-[#1E222D] transition-all sm:text-sm"
                        placeholder="Search ticker (e.g. AAPL.US)"
                        value={searchInput}
                        onChange={(e) => setSearchInput(e.target.value)}
                    />
                </form>
            </div>
        </nav>
    );
}
