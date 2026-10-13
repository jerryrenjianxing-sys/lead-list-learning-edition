const path = require('node:path');
module.exports = {
  packagerConfig: {
    name: 'MediaWorkbench', executableName: 'MediaWorkbench', appBundleId: 'MediaWorkbench.Desktop',
    asar: { unpack: '**/*.node' },
    icon: path.resolve(__dirname, '../webui/public/brand/media-deep-researcher.ico'),
    electronZipDir: path.resolve(__dirname, '../packaging/cache'),
    win32metadata: { CompanyName: 'Media Deep Researcher', ProductName: 'Media Deep Researcher', FileDescription: 'Media Deep Researcher', OriginalFilename: 'MediaWorkbench.exe' },
    ignore: ['^/src($|/)', '^/tests($|/)', '^/scripts($|/)', '^/tsconfig\\.json$', '^/package-lock\\.json$', '^/forge\\.config\\.cjs$', '^/\\.gitignore$']
  },
  makers: [], publishers: []
};
