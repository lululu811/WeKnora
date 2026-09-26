import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles/global.css';

// 顶层错误展示 —— 暴露到 window 以便调试
class TopErrorBoundary extends React.Component<{ children: React.ReactNode }, { err?: Error }> {
  state: { err?: Error } = {};
  static getDerivedStateFromError(err: Error) {
    return { err };
  }
  componentDidCatch(err: Error, info: React.ErrorInfo) {
    console.error('[TopErrorBoundary]', err, info.componentStack);
  }
  render() {
    if (this.state.err) {
      return React.createElement(
        'pre',
        { style: { color: '#ef4444', padding: 24, whiteSpace: 'pre-wrap' } },
        String(this.state.err?.stack ?? this.state.err),
      );
    }
    return this.props.children;
  }
}

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('#root not found');

ReactDOM.createRoot(rootEl).render(
  <TopErrorBoundary>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </TopErrorBoundary>,
);
