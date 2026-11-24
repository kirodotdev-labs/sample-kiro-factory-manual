import { Sha256 } from '@aws-crypto/sha256-js';
import { SignatureV4 } from '@smithy/signature-v4';

/**
 * One SigV4-signed call to the Lambda MicroVMs API, as the function's own role.
 * The runtime-included SDK may predate the lambda-microvms API, so the request is built here.
 */
export async function microvmApi(
  method: string,
  path: string,
  options: { body?: unknown; query?: Record<string, string> } = {},
): Promise<unknown> {
  const body = options.body === undefined ? undefined : JSON.stringify(options.body);
  return awsApi('lambda', {
    method,
    path,
    query: options.query,
    headers: body === undefined ? {} : { 'content-type': 'application/json' },
    body,
  });
}

/** One SigV4-signed call to a regional AWS endpoint, as the function's own role. Returns the parsed JSON body. */
export async function awsApi(
  service: string,
  request: {
    method: string;
    path: string;
    query?: Record<string, string>;
    headers: Record<string, string>;
    body?: string;
  },
): Promise<unknown> {
  const region = setting('AWS_REGION');
  const hostname = `${service}.${region}.amazonaws.com`;
  const { method, path, body } = request;
  const signer = new SignatureV4({
    service,
    region,
    credentials: {
      accessKeyId: setting('AWS_ACCESS_KEY_ID'),
      secretAccessKey: setting('AWS_SECRET_ACCESS_KEY'),
      sessionToken: process.env.AWS_SESSION_TOKEN,
    },
    sha256: Sha256,
  });
  const signed = await signer.sign({
    method,
    protocol: 'https:',
    hostname,
    path,
    query: request.query,
    headers: { host: hostname, ...request.headers },
    body,
  });
  // fetch sets Host from the URL, which is the host that was signed.
  const headers = Object.fromEntries(Object.entries(signed.headers).filter(([name]) => name !== 'host'));
  // Encoded the way SigV4 canonicalizes it (RFC 3986), so the URL sent is the one signed. URLSearchParams
  // form-encodes instead, which differs for an opaque nextToken.
  const query = request.query
    ? `?${Object.entries(request.query)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([k, v]) => `${rfc3986(k)}=${rfc3986(v)}`)
        .join('&')}`
    : '';
  const response = await fetch(`https://${hostname}${path}${query}`, {
    method,
    headers,
    body,
    signal: AbortSignal.timeout(20_000),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path} returned ${response.status}: ${text.slice(0, 2000)}`);
  return text ? JSON.parse(text) : {};
}

export const rfc3986 = (s: string) =>
  encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);

export function setting(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set`);
  return value;
}
