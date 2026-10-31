/** Stand-in for core/notification: a confirmation is always accepted. */
define([], function() {
    return {
        exception: error => {
            const alert = document.createElement('p');
            alert.setAttribute('role', 'alert');
            alert.dataset.notification = '1';
            alert.textContent = error.message || String(error);
            document.body.append(alert);
        },
        saveCancelPromise: () => Promise.resolve(),
    };
});
