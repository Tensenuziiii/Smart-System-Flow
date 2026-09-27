const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');

export function apiUrl(path) {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}