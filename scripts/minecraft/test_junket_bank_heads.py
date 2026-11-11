"""Synthetic bank head-surface and allowed-source URL checks; no artwork loaded."""
import unittest
from build_junket_bank_heads import head_geometry, skin_url


class HeadChecks(unittest.TestCase):
    def test_profile_url_is_scoped_to_minecraft_texture_content(self):
        url,key = skin_url('http://textures.minecraft.net/texture/'+'a'*64)
        self.assertEqual(url,'https://textures.minecraft.net/texture/'+'a'*64)
        for value in ['https://example.com/texture/'+'a'*64,
                      'https://textures.minecraft.net/texture/../../x',
                      'https://textures.minecraft.net/texture/'+'a'*64+'?x=1']:
            with self.assertRaises(ValueError): skin_url(value)

    def test_shell_inflates_without_expanding_uv_and_omits_empty_hat(self):
        geometry = head_geometry('geometry.test',(64,32),False)['minecraft:geometry'][0]
        self.assertEqual(geometry['description']['texture_height'],32)
        self.assertEqual(len(geometry['bones']),1)
        cube = geometry['bones'][0]['cubes'][0]
        self.assertEqual(cube['size'],[8,8,8]);self.assertEqual(cube['uv'],[0,0])
        self.assertAlmostEqual(cube['size'][0]+cube['inflate']*2,8.04)
        hat = head_geometry('geometry.test',(64,64),True)['minecraft:geometry'][0]['bones'][1]['cubes'][0]
        self.assertEqual(hat['uv'],[32,0]);self.assertAlmostEqual(hat['size'][0]+hat['inflate']*2,8.5)


if __name__ == "__main__":
    unittest.main()
