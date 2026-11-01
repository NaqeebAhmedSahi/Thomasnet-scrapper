import unittest

from app.browser import _is_user_chrome, _looks_like_signup, _same_target, access_restricted
from app.chrome_tabs import extract_urls, tab_title


class ChromeTabTests(unittest.TestCase):
    def test_extract_urls_skips_binary_noise(self):
        raw = b"\x00\x01https://www.thomasnet.com/company/walter-stern-inc-423974/profile?x=1\x00https://example.com/a"
        urls = extract_urls(raw)
        self.assertEqual(urls[0], "https://www.thomasnet.com/company/walter-stern-inc-423974/profile?x=1")
        self.assertEqual(urls[1], "https://example.com/a")

    def test_user_chrome_is_the_open_browser_only(self):
        self.assertTrue(
            _is_user_chrome(r'"C:\Program Files\Google\Chrome\Application\chrome.exe" --flag')
        )
        self.assertTrue(_is_user_chrome("/opt/google/chrome/chrome"))
        self.assertFalse(_is_user_chrome("/opt/google/chrome/chrome_crashpad_handler --no-upload"))
        self.assertFalse(
            _is_user_chrome(
                "/opt/google/chrome/chrome --user-data-dir=/home/dev/WorkSpace/thomasnet/.cache/chrome-profile"
            )
        )

    def test_access_restriction_is_recognized(self):
        page = "Access is temporarily restricted. Automated (bot) activity on your network."
        self.assertTrue(access_restricted(page))
        self.assertFalse(access_restricted("Surgical instruments suppliers"))

    def test_signup_page_is_left_open(self):
        self.assertTrue(_looks_like_signup("https://www.thomasnet.com/sign-up", "Create account", 1))
        self.assertTrue(_looks_like_signup("https://accounts.google.com/o/oauth2", "Google", 1))
        self.assertTrue(_looks_like_signup("https://www.thomasnet.com/suppliers", "Results", 2))
        self.assertFalse(_looks_like_signup("https://www.thomasnet.com/suppliers?pg=2", "Surgical instruments", 1))

    def test_same_tab_url(self):
        self.assertTrue(
            _same_target(
                "https://www.thomasnet.com/company/walter-stern-inc-423974/profile?x=1",
                "https://thomasnet.com/company/walter-stern-inc-423974/profile",
            )
        )
        self.assertFalse(_same_target("https://www.thomasnet.com/", "https://www.thomasnet.com/suppliers"))

    def test_company_title(self):
        self.assertEqual(
            tab_title("https://www.thomasnet.com/company/walter-stern-inc-423974/profile"),
            "Walter Stern Inc",
        )


if __name__ == "__main__":
    unittest.main()
