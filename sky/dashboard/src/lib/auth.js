// AuthManager is a singleton class that manages the authentication state of the token.
// It is used to store and retrieve the token from the local storage.
// It is also used to logout the token.

import { BASE_PATH } from '@/data/connectors/constants';

class AuthManager {
  static TOKEN_KEY = 'skypilot_token';

  // Store token
  static setToken(token) {
    if (typeof window !== 'undefined') {
      localStorage.setItem(this.TOKEN_KEY, token);
    }
  }

  // Get token
  static getToken() {
    if (typeof window !== 'undefined') {
      return localStorage.getItem(this.TOKEN_KEY);
    }
    return null;
  }

  // Remove token
  static removeToken() {
    if (typeof window !== 'undefined') {
      localStorage.removeItem(this.TOKEN_KEY);
    }
  }

  // Check if authenticated
  static isAuthenticated() {
    const token = this.getToken();
    return token && token.length > 0;
  }

  // Validate token format
  static validateToken(token) {
    if (!token || typeof token !== 'string') {
      return false;
    }

    // Check SkyPilot token format
    if (token.startsWith('sky_') && token.length > 10) {
      return true;
    }

    return false;
  }

  // Logout
  static logout() {
    this.removeToken();
    if (typeof window !== 'undefined') {
      window.location.href = `${BASE_PATH}/login`;
    }
  }
}

export default AuthManager;
