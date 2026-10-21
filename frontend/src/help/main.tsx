import { createRoot } from 'react-dom/client';
import { MuiRoot } from '../mui';
import { HelpPage } from './HelpPage';

createRoot(document.getElementById('root')!).render(<MuiRoot><HelpPage /></MuiRoot>);
