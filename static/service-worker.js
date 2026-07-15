// Jarvis Service Worker — PWA install ke liye zaroori
// Chrome "Add to Home Screen" tabhi dikhata hai jab ek active service worker ho.
//
// v2 FIX: Pehle iska "fetch" event listener HAR request ko intercept karke
// try-fetch-else-fake-503-response karta tha. Isse kabhi kabhi real API calls
// (jaise naya chat banana — POST /api/chats/new) beech mein hi fail ho jaate
// the aur ek fake "Offline hai" jawab mil jaata tha, chahe internet/server
// bilkul theek ho. Ab yeh service worker sirf install/activate handle karta
// hai (PWA-installable rehne ke liye) — network requests mein KABHI dakhal
// nahi deta, sab kuch seedha browser se server tak jaata hai jaise normal.

const CACHE_NAME = "jarvis-v2";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      // Purane cache versions (agar koi ho) saaf karo
      const keys = await caches.keys();
      await Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)));
      await self.clients.claim();
    })()
  );
});

// Jaan-boojh kar KOI "fetch" event listener nahi hai — is se saari network
// requests bina kisi interception ke seedha jaati hain. Yehi sabse safe/
// predictable behavior hai is app ke liye.
