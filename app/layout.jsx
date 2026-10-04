import './globals.css';

export const metadata = {
  title: 'Read My Zotero',
  description: 'Local Zotero reading workspace'
};

export default function RootLayout({ children }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
