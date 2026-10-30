import {cpSync, existsSync, mkdirSync, rmSync} from 'node:fs';
import {resolve} from 'node:path';

const source = resolve('node_modules/cesium/Build/Cesium');
const target = resolve('public/cesiumStatic');
if (!existsSync(source)) {
  console.error(`Cesium static assets not found at ${source}. Run npm install first.`);
  process.exit(1);
}
rmSync(target, {recursive: true, force: true});
mkdirSync(target, {recursive: true});
cpSync(source, target, {recursive: true});
console.log(`Copied Cesium runtime assets to ${target}`);
