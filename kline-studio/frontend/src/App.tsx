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
          {/* `/k/:ticker` 兼容两种 URL：thscode 合并 (`/k/600519.SH`) 与
              拆分 (`/k/600519/SH`)。后者需要新加一层路由，但实际用法
              99% 是 thscode 合并形式，所以这里只覆盖最常见一种。 */}
          <Route path="/k/:ticker" element={<KLinePage />} />
          <Route path="/k/:ticker/:exchange" element={<KLinePage />} />
        </Route>
      </Routes>
    </StockNavProvider>
  );
}
