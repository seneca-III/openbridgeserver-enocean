export const AUTH_TOKEN_REFRESHED_EVENT = 'obs:auth-token-refreshed'

export function notifyAuthTokenRefreshed() {
  window.dispatchEvent(new Event(AUTH_TOKEN_REFRESHED_EVENT))
}
