document.documentElement.classList.add('js');

const navigationToggle = document.querySelector('.nav-toggle');
const siteNavigation = document.querySelector('.site-nav');

if (navigationToggle && siteNavigation) {
  navigationToggle.addEventListener('click', () => {
    const isOpen = navigationToggle.getAttribute('aria-expanded') === 'true';
    navigationToggle.setAttribute('aria-expanded', String(!isOpen));
    siteNavigation.classList.toggle('is-open', !isOpen);
  });

  siteNavigation.addEventListener('click', (event) => {
    if (event.target.closest('a')) {
      navigationToggle.setAttribute('aria-expanded', 'false');
      siteNavigation.classList.remove('is-open');
    }
  });
}

const filterButtons = document.querySelectorAll('[data-project-filter]');
const filterableProjects = document.querySelectorAll('[data-project-tags]');

filterButtons.forEach((button) => {
  button.addEventListener('click', () => {
    const selectedFilter = button.dataset.projectFilter;

    filterButtons.forEach((item) => {
      const isSelected = item === button;
      item.classList.toggle('is-active', isSelected);
      item.setAttribute('aria-pressed', String(isSelected));
    });

    filterableProjects.forEach((project) => {
      const tags = project.dataset.projectTags.split(' ');
      project.hidden = selectedFilter !== 'all' && !tags.includes(selectedFilter);
    });
  });
});
