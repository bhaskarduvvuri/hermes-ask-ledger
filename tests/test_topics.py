import unittest

from plugin.topics import guess_topic


class TopicTest(unittest.TestCase):
    def test_keyword_hits(self):
        self.assertEqual(guess_topic("Can you deploy the staging build?"), "deploy")
        self.assertEqual(guess_topic("There's a bug in the login form"), "bug")
        self.assertEqual(guess_topic("Draft an email to the landlord"), "email")

    def test_deterministic(self):
        self.assertEqual(guess_topic("Summarise this paper on graphs"), guess_topic("Summarise this paper on graphs"))

    def test_empty_is_general(self):
        self.assertEqual(guess_topic(""), "general")
        self.assertEqual(guess_topic("?? !!"), "general")

    def test_returns_short_lowercase_word(self):
        t = guess_topic("Translate the Portuguese brochure")
        self.assertTrue(t.islower())
        self.assertLessEqual(len(t), 20)


if __name__ == "__main__":
    unittest.main()
