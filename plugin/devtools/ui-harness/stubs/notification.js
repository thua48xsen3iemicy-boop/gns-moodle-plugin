/** Stand-in for core/notification: a confirmation is always accepted. */
define([], function() {
    return {
        saveCancelPromise: () => Promise.resolve(),
    };
});
