import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App.js';
import { SnapshotProvider } from './lib/snapshot.js';
import './styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('#root không tồn tại');
createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <SnapshotProvider>
        <App />
      </SnapshotProvider>
    </BrowserRouter>
  </StrictMode>,
);
