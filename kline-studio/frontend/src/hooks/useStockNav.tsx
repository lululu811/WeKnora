import React, { createContext, useContext, useState, useCallback, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';

export interface NavSymbol {
  ticker: string;
  exchange: string;
  name?: string | null;
}

interface StockNavContextValue {
  symbols: NavSymbol[];
  setSymbols: (symbols: NavSymbol[]) => void;
  nextStock: () => void;
  prevStock: () => void;
  focusSearch: () => void;
  registerSearchInput: (ref: HTMLInputElement | null) => void;
  showHelp: boolean;
  setShowHelp: (show: boolean) => void;
  currentTicker?: string;
  currentExchange?: string;
}

const StockNavContext = createContext<StockNavContextValue | null>(null);

export function StockNavProvider({ children }: { children: React.ReactNode }) {
  const [symbols, setSymbols] = useState<NavSymbol[]>([]);
  const [showHelp, setShowHelp] = useState(false);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const navigate = useNavigate();
  const location = useLocation();

  const match = location.pathname.match(/\/k\/([^/]+)\/([^/]+)/);
  const currentTicker = match ? match[1] : undefined;
  const currentExchange = match ? match[2] : undefined;

  const registerSearchInput = useCallback((ref: HTMLInputElement | null) => {
    searchInputRef.current = ref;
  }, []);

  const focusSearch = useCallback(() => {
    if (searchInputRef.current) {
      searchInputRef.current.focus();
      searchInputRef.current.select();
    }
  }, []);

  const nextStock = useCallback(() => {
    if (symbols.length === 0) return;
    const currentIdx = symbols.findIndex(
      (s) => s.ticker === currentTicker && s.exchange === currentExchange,
    );
    const nextIdx = currentIdx >= 0 && currentIdx < symbols.length - 1 ? currentIdx + 1 : 0;
    const target = symbols[nextIdx];
    if (target) {
      navigate(`/k/${target.ticker}/${target.exchange}`);
    }
  }, [symbols, currentTicker, currentExchange, navigate]);

  const prevStock = useCallback(() => {
    if (symbols.length === 0) return;
    const currentIdx = symbols.findIndex(
      (s) => s.ticker === currentTicker && s.exchange === currentExchange,
    );
    const prevIdx = currentIdx > 0 ? currentIdx - 1 : symbols.length - 1;
    const target = symbols[prevIdx];
    if (target) {
      navigate(`/k/${target.ticker}/${target.exchange}`);
    }
  }, [symbols, currentTicker, currentExchange, navigate]);

  return (
    <StockNavContext.Provider
      value={{
        symbols,
        setSymbols,
        nextStock,
        prevStock,
        focusSearch,
        registerSearchInput,
        showHelp,
        setShowHelp,
        currentTicker,
        currentExchange,
      }}
    >
      {children}
    </StockNavContext.Provider>
  );
}

export function useStockNav() {
  const ctx = useContext(StockNavContext);
  if (!ctx) throw new Error('useStockNav must be used within StockNavProvider');
  return ctx;
}
