/* MUI למסך: ערכת נושא עם הצבעים והגופן של המערכת, ימין-לשמאל, בלי "אדוות לחיצה".
 *
 * הסגנונות של MUI נכנסים לשכבת CSS נמוכה (@layer mui). העיצוב הקיים (/static/app.css) אינו בשכבה,
 * ולכן תמיד גובר — כך המראה נשאר בדיוק כמו קודם, ו-MUI מוסיף את ההתנהגות: חלונות עם פוקוס שנשאר
 * בפנים, Esc, נגישות. */
import type { ReactNode } from 'react';
import createCache from '@emotion/cache';
import { CacheProvider } from '@emotion/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { prefixer } from 'stylis';
import rtlPlugin from 'stylis-plugin-rtl';

const cache = createCache({ key: 'mui', stylisPlugins: [prefixer, rtlPlugin] });
const insert = cache.insert;
cache.insert = (...args) => {
  const serialized = args[1];
  if (!/^@layer\s+[^{]*$/.test(serialized.styles)) serialized.styles = `@layer mui {${serialized.styles}}`;
  return insert(...args);
};

export const theme = createTheme({
  direction: 'rtl',
  palette: {
    primary: { main: '#0068f5', dark: '#015dd9' },
    secondary: { main: '#0c306a' },
    warning: { main: '#a8540a' },
    success: { main: '#2d7a4f' },
    text: { primary: '#0c3058', secondary: '#5878a4' },
    background: { default: '#f7f9fd', paper: '#ffffff' },
  },
  typography: { fontFamily: '"Rubik", "Segoe UI", Arial, sans-serif' },
  shape: { borderRadius: 10 },
  components: {
    // כפתור של MUI מתנהג כמו כפתור רגיל של הדפדפן (revert), ו-app.css מעצב אותו בדיוק כמו קודם
    MuiButtonBase: {
      defaultProps: { disableRipple: true, disableTouchRipple: true },
      styleOverrides: {
        root: {
          display: 'revert', alignItems: 'revert', justifyContent: 'revert', position: 'revert',
          verticalAlign: 'revert', userSelect: 'revert', boxSizing: 'revert', margin: 'revert', padding: 'revert',
          border: 'revert', borderRadius: 'revert', backgroundColor: 'revert', color: 'revert', outline: 'revert',
          textDecoration: 'revert', cursor: 'revert', MozAppearance: 'revert', WebkitAppearance: 'revert',
          WebkitTapHighlightColor: 'revert',
        },
      },
    },
    MuiModal: { defaultProps: { disableScrollLock: true, hideBackdrop: true, disablePortal: false } },
  },
});

export function MuiRoot({ children }: { children: ReactNode }) {
  return (
    <CacheProvider value={cache}>
      <ThemeProvider theme={theme}>{children}</ThemeProvider>
    </CacheProvider>
  );
}
