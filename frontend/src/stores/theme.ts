import { defineStore } from 'pinia';
import { reactive } from 'vue';

import { useThemeVars } from '../composables/useTheme';

export const useThemeStore = defineStore('theme', () => {
  const themeVars = reactive<Record<string, string>>({});

  function init() {
    Object.assign(themeVars, useThemeVars());
  }

  return {
    themeVars,
    init,
  };
});
