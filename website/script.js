const root = document.documentElement;
const themeButton = document.querySelector('.theme-toggle');
const storedTheme = localStorage.getItem('apex-theme');
if (storedTheme === 'light') root.dataset.theme = 'light';
themeButton.addEventListener('click', () => {
  const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
  root.dataset.theme = next;
  localStorage.setItem('apex-theme', next);
});
const menuButton = document.querySelector('.menu-toggle');
const links = document.querySelector('.nav-links');
menuButton.addEventListener('click', () => {
  links.classList.toggle('open');
});
