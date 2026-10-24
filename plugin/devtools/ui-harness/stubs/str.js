/** Stand-in for core/str: strings of the plugin from lang/<lang>/gns3lab.php, built by build.js. */
define([], function() {
    const lang = (window.GNS3LAB_STUB || {}).lang || 'en';
    const load = fetch(`strings.${lang}.json`).then(r => r.json());
    return {
        getStrings: requests => load.then(all => requests.map(r => {
            if (!(r.key in all)) {
                console.error('Missing string', r.key);
            }
            return all[r.key] || '[[' + r.key + ']]';
        })),
    };
});
