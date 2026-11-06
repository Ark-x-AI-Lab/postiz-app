const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '../..');
const projectRequire = createRequire(path.join(root, 'package.json'));
const options = { timeout: 15000 };

// Load only installed libraries and the sanitizer, never application bootstrap.
// No dependency fallbacks, mocks, network resources or generated files are used.
async function importInstalled(requireFrom, name) {
  return import(pathToFileURL(requireFrom.resolve(name)).href);
}

async function loadSanitizer() {
  const ts = projectRequire('typescript');
  assert.equal(ts.version, '5.5.4');
  const filename = path.join(root, 'libraries/helpers/src/utils/sanitize.post.content.ts');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2021,
      module: ts.ModuleKind.CommonJS,
      esModuleInterop: true,
    },
  });
  assert.deepEqual(
    compiled.diagnostics.filter(item => item.category === ts.DiagnosticCategory.Error),
    []
  );

  const purify = await importInstalled(projectRequire, 'isomorphic-dompurify');
  const parse5 = await importInstalled(projectRequire, 'parse5');
  const dependencies = new Map([
    ['isomorphic-dompurify', purify.default ?? purify],
    ['parse5', parse5],
  ]);
  const module = { exports: {} };
  const actualDependency = name => {
    assert.ok(dependencies.has(name), `Unexpected sanitizer dependency: ${name}`);
    return dependencies.get(name);
  };
  new Function('require', 'module', 'exports', compiled.outputText)(
    actualDependency, module, module.exports
  );
  return module.exports;
}

let sanitizer;
const actualSanitizer = () => (sanitizer ??= loadSanitizer());

test('native acceptance uses the pinned Node and TypeScript runtime', options, () => {
  assert.equal(process.versions.node, '22.23.4');
  assert.equal(projectRequire('typescript').version, '5.5.4');
});

test('bcrypt hashes and accepts the correct password while rejecting the wrong one', options, () => {
  const bcrypt = projectRequire('bcrypt');
  const password = 'synthetic-native-runtime-password';
  const hash = bcrypt.hashSync(password, 4);
  assert.notEqual(hash, password);
  assert.equal(bcrypt.compareSync(password, hash), true);
  assert.equal(bcrypt.compareSync('synthetic-wrong-password', hash), false);
});

test('canvas has its native binding and renders a two-pixel-square PNG', options, () => {
  const packagePath = projectRequire.resolve('canvas/package.json');
  assert.equal(projectRequire(packagePath).version, '3.2.3');
  const binding = path.join(path.dirname(packagePath), 'build/Release/canvas.node');
  assert.ok(fs.statSync(binding).isFile(), 'Canvas native binding must be a file');
  fs.accessSync(binding, fs.constants.R_OK);

  const canvas = projectRequire('canvas').createCanvas(2, 2);
  const context = canvas.getContext('2d');
  context.fillStyle = '#123456';
  context.fillRect(0, 0, 2, 2);
  assert.deepEqual(Array.from(context.getImageData(0, 0, 1, 1).data), [18, 52, 86, 255]);
  const png = canvas.toBuffer('image/png');
  assert.ok(png.length > 24);
  assert.deepEqual(png.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  assert.equal(png.readUInt32BE(16), 2);
  assert.equal(png.readUInt32BE(20), 2);
});

test('the real sanitizer preserves healthy markup and parse5 decodes its plain text', options, async () => {
  const { sanitizePostContent, postContentPlainText } = await actualSanitizer();
  const html = '<p>Hello <strong>team</strong> &amp; friends.</p>';
  assert.equal(sanitizePostContent(html), html);
  assert.equal(postContentPlainText(html), 'Hello team & friends.');
});

test('the real sanitizer removes unsafe markup without losing healthy text', options, async () => {
  const { sanitizePostContent, postContentPlainText } = await actualSanitizer();
  const html = '<p onclick="alert(1)">Safe <strong>text</strong></p>' +
    '<script>alert(1)</script><a href="javascript:alert(1)">Open</a>' +
    '<img src="data:image/png;base64,AA==" onerror="alert(1)">';
  const clean = sanitizePostContent(html);
  assert.doesNotMatch(clean, /<script\b|\bonclick\s*=|\bonerror\s*=|javascript:|data:image|<img\b/i);
  assert.match(clean, /<strong>text<\/strong>/);
  assert.equal(postContentPlainText(html), 'Safe textOpen');
  assert.equal(sanitizePostContent(null), '');
});

async function checkJsdom(requireFrom, version) {
  assert.equal(requireFrom('jsdom/package.json').version, version);
  const loaded = await importInstalled(requireFrom, 'jsdom');
  const { JSDOM } = loaded.default ?? loaded;
  const dom = new JSDOM('<!doctype html><h1>Synthetic heading</h1>' +
    '<p>Hello <strong>team</strong> &amp; friends.</p>' +
    '<script>window.nativeProbeExecuted = true;</script>');
  try {
    assert.equal(dom.window.document.querySelector('h1').textContent, 'Synthetic heading');
    assert.equal(dom.window.document.querySelector('p').textContent, 'Hello team & friends.');
    assert.equal(dom.window.nativeProbeExecuted, undefined);
  } finally {
    dom.window.close();
  }
}

test('the real root jsdom parses synthetic heading and text without executing scripts', options, async () => {
  await checkJsdom(projectRequire, '22.1.0');
});

test('DOMPurify\'s real nested jsdom parses synthetic heading and text without executing scripts', options, async () => {
  const domPurifyRequire = createRequire(projectRequire.resolve('isomorphic-dompurify'));
  await checkJsdom(domPurifyRequire, '29.1.0');
});
