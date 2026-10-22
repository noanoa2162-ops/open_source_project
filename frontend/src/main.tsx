import { createRoot } from 'react-dom/client';
import { MuiRoot } from './mui';
import { App } from './App';
import { S } from './store';
import * as actions from './actions';

// לבדיקות בדפדפן בלבד (בלי מודל ובלי עלות): גישה למצב המסך כשמבקשים במפורש
try { if (localStorage.getItem('debugUI') === '1') (window as any).__ui = { S, actions }; } catch { /* דפדפן פרטי */ }

createRoot(document.getElementById('root')!).render(<MuiRoot><App /></MuiRoot>);
