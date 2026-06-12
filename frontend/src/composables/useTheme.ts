import { reactive } from 'vue';
import {
  bindThemeParamsCssVars,
  mountThemeParamsSync,
  themeParamsAccentTextColor,
  themeParamsBackgroundColor,
  themeParamsButtonColor,
  themeParamsButtonTextColor,
  themeParamsDestructiveTextColor,
  themeParamsHintColor,
  themeParamsLinkColor,
  themeParamsSecondaryBackgroundColor,
  themeParamsSectionBackgroundColor,
  themeParamsSectionSeparatorColor,
  themeParamsTextColor,
} from '@telegram-apps/sdk';

function color(read: () => string | undefined, fallback: string): string {
  try {
    return read() || fallback;
  } catch {
    return fallback;
  }
}

export function useThemeVars() {
  const themeVars = reactive<Record<string, string>>({});

  function applyTheme() {
    const background = color(themeParamsBackgroundColor, '#ffffff');
    const background2 = color(
      () => themeParamsSectionBackgroundColor() || themeParamsSecondaryBackgroundColor(),
      '#f7f8fa',
    );
    const text = color(themeParamsTextColor, '#323233');
    const hint = color(themeParamsHintColor, '#969799');
    const primary = color(() => themeParamsButtonColor() || themeParamsAccentTextColor(), '#2481cc');
    const primaryText = color(themeParamsButtonTextColor, '#ffffff');
    const danger = color(themeParamsDestructiveTextColor, '#ee0a24');
    const border = color(themeParamsSectionSeparatorColor, '#ebedf0');
    const link = color(themeParamsLinkColor, primary);

    Object.assign(themeVars, {
      background,
      background2,
      textColor: text,
      textColor2: hint,
      primaryColor: primary,
      buttonPrimaryBackground: primary,
      buttonPrimaryColor: primaryText,
      dangerColor: danger,
      cellBackground: background2,
      borderColor: border,
      cellBorderColor: border,
    });

    document.documentElement.style.setProperty('--van-link-color', link);
  }

  mountThemeParamsSync.ifAvailable();
  bindThemeParamsCssVars.ifAvailable();
  applyTheme();

  return themeVars;
}
