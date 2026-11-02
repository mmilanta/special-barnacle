/** @type {import('tailwindcss').Config} */
module.exports = {
    content: ["../templates/*.html"],
    theme: {
        extend: {
            fontFamily: {
                serif: ['"Libre Baskerville"', 'Georgia', 'serif'],
            },
        },
    },
    plugins: [],
    safelist: [
        'ml-6',
        'list-disc',
      ],
};
