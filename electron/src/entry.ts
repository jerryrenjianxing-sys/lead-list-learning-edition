// The native activation handler runs before Electron startup and never auto-applies.
import { VelopackApp } from 'velopack';
VelopackApp.build().setAutoApplyOnStartup(false).run();
require('./main');
