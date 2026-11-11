package com.github.miyako.sonar.fourd;

import org.junit.jupiter.api.Test;
import org.sonar.api.Plugin;
import org.sonar.api.SonarRuntime;
import org.sonar.api.config.PropertyDefinition;
import org.sonar.api.config.PropertyDefinition.ConfigScope;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

class FourDPluginTest {
  @Test
  void registersOnlyLanguageAndSuffixProperty() {
    Plugin.Context context = new Plugin.Context(mock(SonarRuntime.class));
    new FourDPlugin().define(context);

    assertEquals(2, context.getExtensions().size());
    assertTrue(context.getExtensions().contains(FourDLanguage.class));
    PropertyDefinition property = (PropertyDefinition) context.getExtensions().get(1);
    assertEquals("sonar.fourd.file.suffixes", property.key());
    assertEquals("4dm", property.defaultValue());
    assertTrue(property.multiValues());
    assertEquals("4D", property.category());
    assertTrue(property.configScopes().contains(ConfigScope.PROJECT));
    assertTrue(property.global());
  }
}
