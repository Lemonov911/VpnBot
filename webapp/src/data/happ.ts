/**
 * Канонические ссылки на приложение Happ — единственный источник правды.
 *
 * ⚠️ В РФ App Store это ОТДЕЛЬНОЕ приложение («Happ Proxy Utility Plus»,
 * id6746188973, путь /ru/). Глобальное (id6504287215, /us/) в российском
 * App Store недоступно — рус-юзер по нему упирается в «недоступно в регионе».
 * Поэтому iOS-ссылки разнесены на iosRu / iosGlobal.
 *
 * Раньше глобальная ссылка была захардкожена в 4 местах (Instructions, VPN,
 * Plans, TrialSuccessSheet) — отсюда массовые «не могу установить» у РФ.
 * Меняешь ссылку — меняй ТОЛЬКО здесь.
 *
 * `site` — официальная страница happ.su, сама показывает нужный стор по
 * региону. Используем там, где места на две iOS-кнопки нет.
 */
export const HAPP_LINKS = {
  iosRu:      'https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973',
  iosGlobal:  'https://apps.apple.com/us/app/happ-proxy-utility/id6504287215',
  android:    'https://play.google.com/store/apps/details?id=com.happproxy',
  androidApk: 'https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk',
  site:       'https://happ.su',
} as const
