import type { Options } from '@wdio/types';

const isBrowserStack = !!process.env.BROWSERSTACK_USERNAME;

const bsUser = process.env.BROWSERSTACK_USERNAME || '';
const bsKey = process.env.BROWSERSTACK_ACCESS_KEY || '';
const platform = process.env.W2V_PLATFORM || 'Android';

// BrowserStack capabilities
const bsCapabilities = [
  {
    platformName: platform,
    'appium:app': process.env.W2V_APP_URL || process.env.W2V_APP_PATH || '',
    'appium:deviceName': process.env.W2V_DEVICE || '',
    'appium:automationName': platform === 'iOS' ? 'XCUITest' : 'UiAutomator2',
    'appium:newCommandTimeout': 240,
    'bstack:options': {
      userName: bsUser,
      accessKey: bsKey,
      video: true,
      networkLogs: false,
      debug: false,
      appiumVersion: '2.0.0',
    },
  },
];

// Local capabilities
const localCapabilities = [
  {
    platformName: platform,
    'appium:deviceName': process.env.W2V_DEVICE || '',
    'appium:app': process.env.W2V_APP_PATH || '',
    'appium:automationName': platform === 'iOS' ? 'XCUITest' : 'UiAutomator2',
    'appium:newCommandTimeout': 240,
  },
];

export const config: Options.Testrunner = {
  runner: 'local',
  autoCompileOpts: {
    tsNodeOpts: {
      project: './tsconfig.json',
    },
  },
  specs: ['./parts/**/*.test.ts'],
  maxInstances: 1,
  capabilities: isBrowserStack ? bsCapabilities : localCapabilities,

  // When using BrowserStack, connect to cloud hub instead of local Appium
  ...(isBrowserStack
    ? {
        hostname: 'hub-cloud.browserstack.com',
        port: 443,
        protocol: 'https' as const,
        path: '/wd/hub',
        // No local Appium service needed
        services: [],
      }
    : {
        services: [
          [
            'appium',
            {
              args: { relaxedSecurity: true },
              command: 'appium',
            },
          ],
        ],
      }),

  framework: 'mocha',
  reporters: ['spec'],
  mochaOpts: {
    ui: 'bdd',
    timeout: 240000,
  },
  connectionRetryTimeout: 120000,
  connectionRetryCount: 3,
};
