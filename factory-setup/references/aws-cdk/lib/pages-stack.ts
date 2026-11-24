import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import { S3BucketOrigin } from 'aws-cdk-lib/aws-cloudfront-origins';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { FactorySettings, RUNS_PREFIX, STATE_PREFIX, pagesBucketName } from './settings';

/**
 * Pages load their CSS and JS from this origin, which the s3 integration's page packaging assumes.
 * base-uri, form-action and frame-ancestors don't fall back to default-src, so each is set.
 */
const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "media-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

/**
 * The factory's pages, run records and worker state: one private bucket behind CloudFront.
 *
 * Every request is refused until viewer authentication replaces the Locked function on the default
 * behavior: use what the organization already has, and have new auth code security reviewed before it
 * serves pages. A public project's pages drop the function instead. Run records carry traces, so the
 * distribution may read only the page prefixes; the runs/ and state/ behaviors refuse them as well.
 */
export class FactoryPagesStack extends Stack {
  constructor(scope: Construct, id: string, props: StackProps & { settings: FactorySettings }) {
    super(scope, id, props);

    // RETAIN with a fixed name: the host stack and the instance's publishing config name it.
    const bucket = new s3.Bucket(this, 'Bucket', {
      bucketName: pagesBucketName(props.settings, this.account),
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
      lifecycleRules: [
        // Every run rewrites its keys.
        { noncurrentVersionExpiration: Duration.days(30) },
        // Nothing else deletes a run record. Keep at least the report's `days` and learn's window.
        { prefix: RUNS_PREFIX, expiration: Duration.days(90) },
      ],
    });

    const headers = new cloudfront.ResponseHeadersPolicy(this, 'Headers', {
      // A run sets each page's metadata, so a long Cache-Control it writes would keep a bad page in
      // browsers after it is replaced.
      customHeadersBehavior: {
        customHeaders: [{ header: 'Cache-Control', value: 'no-store', override: true }],
      },
      securityHeadersBehavior: {
        contentSecurityPolicy: { contentSecurityPolicy: CSP, override: true },
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.NO_REFERRER, override: true },
        strictTransportSecurity: { accessControlMaxAge: Duration.days(365), includeSubdomains: true, override: true },
      },
    });

    const locked = new cloudfront.Function(this, 'Locked', {
      comment: 'Refuses every request',
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        "function handler() { return { statusCode: 403, statusDescription: 'Forbidden' }; }",
      ),
    });
    const refused = {
      functionAssociations: [{ function: locked, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
    };
    // Not withOriginAccessControl: it grants the distribution every key, and path patterns alone would
    // then be all that keeps run records from viewers.
    const access = new cloudfront.S3OriginAccessControl(this, 'OriginAccess', {
      signing: cloudfront.Signing.SIGV4_ALWAYS,
    });
    const behavior: cloudfront.BehaviorOptions = {
      origin: S3BucketOrigin.withBucketDefaults(bucket, { originAccessControlId: access.originAccessControlId }),
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      // The factory rewrites the same keys every run; a cached copy would hide a fresh one.
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      responseHeadersPolicy: headers,
    };

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: `${props.settings.name} pages`,
      defaultBehavior: { ...behavior, ...refused },
      additionalBehaviors: {
        [`${RUNS_PREFIX}*`]: { ...behavior, ...refused },
        [`${STATE_PREFIX}*`]: { ...behavior, ...refused },
      },
      // Applies only with a custom domain and certificate; the default *.cloudfront.net one accepts TLSv1.
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
    });

    bucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'DistributionReadsPages',
        principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
        actions: ['s3:GetObject'],
        resources: props.settings.pagePrefixes.map((prefix) => bucket.arnForObjects(`${prefix}*`)),
        conditions: {
          StringEquals: {
            'AWS:SourceArn': `arn:${this.partition}:cloudfront::${this.account}:distribution/${distribution.distributionId}`,
          },
        },
      }),
    );

    new CfnOutput(this, 'PagesBucket', { value: bucket.bucketName });
    new CfnOutput(this, 'PagesBaseUrl', { value: `https://${distribution.distributionDomainName}/` });
  }
}
