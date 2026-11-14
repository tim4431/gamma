// Where a host is, read from its name alone (no DNS): the shell's copy of
// backend/gamma/host_scope.py `literal`, so a typed address gets its scheme
// (registry.withScheme) and the launcher offers http:// (lib/startup.js)
// by the rule the server refuses plain http by (docs/dev/https.md "Plain
// http"). backend/tests/data/host_scope_cases.json holds the cases both
// read (test/hostScope.test.js), so the two tables cannot drift. The shell
// never resolves a name: a name's answer is the server's
// (POST /api/remote-servers/check).
//
// loopback: 127.0.0.0/8, ::1, localhost. lan: an IP in LAN_NETS, a
// single-label name, a LAN_SUFFIXES name, *.localhost. far: any other IP
// literal. name: any other dotted name.

const net = require('net');

const LOOPBACK = 'loopback';
const LAN = 'lan';
const NAME = 'name';
const FAR = 'far';

const LAN_NETS = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '169.254.0.0/16', 'fc00::/7', 'fe80::/10'];
const LAN_SUFFIXES = ['.local', '.lan', '.home.arpa', '.internal'];

function blocks(list) {
  const b = new net.BlockList();
  for (const cidr of list) {
    const [addr, bits] = cidr.split('/');
    b.addSubnet(addr, Number(bits), net.isIPv6(addr) ? 'ipv6' : 'ipv4');
  }
  return b;
}
const LOOPBACK_NETS = blocks(['127.0.0.0/8', '::1/128']);
const LAN_BLOCKS = blocks(LAN_NETS);

// A host as a URL's hostname gives it: lowercase, IPv6 brackets and a
// trailing dot dropped.
const bare = (host) => String(host || '').trim().toLowerCase().replace(/^\[(.*)\]$/, '$1').replace(/\.$/, '');

function literal(host) {
  const h = bare(host);
  const family = net.isIP(h);
  if (family) {
    const type = family === 6 ? 'ipv6' : 'ipv4';
    if (LOOPBACK_NETS.check(h, type)) return LOOPBACK;
    return LAN_BLOCKS.check(h, type) ? LAN : FAR;
  }
  if (h === 'localhost') return LOOPBACK;
  if (!h.includes('.') || h.endsWith('.localhost') || LAN_SUFFIXES.some((s) => h.endsWith(s))) return LAN;
  return NAME;
}

module.exports = { LOOPBACK, LAN, NAME, FAR, LAN_NETS, LAN_SUFFIXES, literal };
