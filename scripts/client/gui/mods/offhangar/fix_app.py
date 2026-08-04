import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_app = """						class FakeAppearance(object):
							def __init__(self):
								class FakeCompound(object):
									def node(self, name):
										if name == 'HP_gunJoint': return loaded_models['gun_node_matrix']
										if name == 'HP_turretJoint': return loaded_models.get('hull').node(name) if loaded_models.get('hull') else None
										return mock_veh.model.node(name)
									@property
									def position(self): return mock_veh.position
									@property
									def matrix(self): return mock_veh.matrix
								self.compoundModel = FakeCompound()
								self.modelsDesc = {'gun': {'model': loaded_models.get('gun')}}
							def changeVisibility(self, modelName, modelVisible, attachmentsVisible):
								is_sniper = not modelVisible
								c_mdl = loaded_models.get('chassis')
								if hasattr(h_mdl, 'visible'): h_mdl.visible = not is_sniper
								if hasattr(t_mdl, 'visible'): t_mdl.visible = not is_sniper
								if hasattr(g_mdl, 'visible'): g_mdl.visible = not is_sniper
							def hideIfExistFor(self, vehicle):
								pass"""

good_app = """						class FakeAppearance(object):
							def __init__(self):
								class FakeCompound(object):
									def node(self, name):
										if name == 'HP_gunJoint': return loaded_models['gun_node_matrix']
										if name == 'HP_turretJoint': return loaded_models.get('hull').node(name) if loaded_models.get('hull') else None
										return mock_veh.model.node(name)
									@property
									def position(self): return mock_veh.position
									@property
									def matrix(self): return mock_veh.matrix
								self.compoundModel = FakeCompound()
								self.modelsDesc = {'gun': {'model': loaded_models.get('gun')}}
							def changeVisibility(self, modelName, modelVisible, attachmentsVisible):
								try:
									is_sniper = not modelVisible
									if 'hull' in loaded_models: loaded_models['hull'].visible = not is_sniper
									if 'turret' in loaded_models: loaded_models['turret'].visible = not is_sniper
									if 'gun' in loaded_models: loaded_models['gun'].visible = not is_sniper
								except:
									pass
							def hideIfExistFor(self, vehicle):
								pass
							def __getattr__(self, item):
								# Prevent any AttributeError from crashing caller
								return lambda *a, **kw: None"""

if bad_app in content:
    content = content.replace(bad_app, good_app)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Fixed FakeAppearance!")
else:
    print("FakeAppearance not found!")
