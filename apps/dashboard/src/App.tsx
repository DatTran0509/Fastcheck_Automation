import { NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { useSnapshot } from './lib/snapshot.js';
import { OverviewPage } from './pages/OverviewPage.js';
import { StationsPage } from './pages/StationsPage.js';
import { ProfilesPage } from './pages/ProfilesPage.js';
import { JobsPage } from './pages/JobsPage.js';
import { GuidePage } from './pages/GuidePage.js';

interface NavEntry {
  to: string;
  label: string;
  icon: string;
  title: string;
  sub: string;
  end?: boolean;
}

const NAV: NavEntry[] = [
  { to: '/', label: 'Tổng quan', icon: '📊', title: 'Tổng quan', sub: 'Tỷ lệ kết quả & sức khoẻ hệ thống', end: true },
  { to: '/stations', label: 'Stations', icon: '🖥️', title: 'Station Management', sub: 'Máy trạm kết nối realtime' },
  { to: '/profiles', label: 'Pool & Tài khoản', icon: '👤', title: 'Pool & Tài khoản', sub: 'Nạp/đăng nhập tài khoản + danh sách pool' },
  { to: '/jobs', label: 'Kết quả', icon: '🔎', title: 'Kết quả check', sub: 'Gửi check + lịch sử (lọc/tìm/Excel)' },
  { to: '/guide', label: 'Hướng dẫn', icon: '📖', title: 'Hướng dẫn sử dụng', sub: 'Các tính năng & cách test thực tế' },
];

export function App(): JSX.Element {
  const { live } = useSnapshot();
  const { pathname } = useLocation();
  const active = NAV.find((n) => (n.end ? pathname === n.to : pathname.startsWith(n.to))) ?? NAV[0];

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-logo">F</div>
          <div>
            <div className="brand-name">FastCheck</div>
            <div className="brand-sub">Control Center</div>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
              <span className="nav-ico">{n.icon}</span>
              <span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          {live ? '● Realtime đang chạy' : '○ Mất kết nối realtime'}
          <br />
          LIVE / DEAD / INCONCLUSIVE
        </div>
      </aside>

      <div className="content">
        <header className="topbar">
          <div>
            <h1>{active.title}</h1>
            <div className="topbar-sub">{active.sub}</div>
          </div>
          <div className="topbar-right">
            <span className={`conn ${live ? 'on' : 'off'}`}>
              <span className="dot" />
              {live ? 'Realtime' : 'Mất kết nối'}
            </span>
          </div>
        </header>

        <main className="page">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/stations" element={<StationsPage />} />
            <Route path="/profiles" element={<ProfilesPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/guide" element={<GuidePage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
