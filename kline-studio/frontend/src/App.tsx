import { Routes, Route } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { HomePage } from '@/pages/HomePage';
import { KLinePage } from '@/pages/KLinePage';
import { StockNavProvider } from '@/hooks/useStockNav';

export default function App() {
  return (
    <StockNavProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<HomePage />} />
          <Route path="/k/:ticker/:exchange" element={<KLinePage />} />
        </Route>
      </Routes>
    </StockNavProvider>
  );
}
