package com.github.miyako.sonar.fourd;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.sonar.api.config.Configuration;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class FourDLanguageTest {
  private final Configuration configuration = mock(Configuration.class);
  private final FourDLanguage language = new FourDLanguage(configuration);

  @BeforeEach
  void configureDefaults() {
    when(configuration.getStringArray(FourDLanguage.FILE_SUFFIXES_KEY)).thenReturn(new String[0]);
  }

  @Test
  void identifiesFourDAndPublishesFilesWithoutSensorData() {
    assertEquals("fourd", language.getKey());
    assertEquals("4D", language.getName());
    assertTrue(language.publishAllFiles());
    assertArrayEquals(new String[0], language.filenamePatterns());
  }

  @Test
  void defaultsToFourDmWithoutLeadingDot() {
    assertArrayEquals(new String[] {"4dm"}, language.getFileSuffixes());
  }

  @Test
  void acceptsConfiguredSuffixes() {
    when(configuration.getStringArray(FourDLanguage.FILE_SUFFIXES_KEY))
        .thenReturn(new String[] {"4dm", " custom4d ", ""});
    assertArrayEquals(new String[] {"4dm", "custom4d"}, language.getFileSuffixes());
  }

  @Test
  void fallsBackForBlankConfiguration() {
    when(configuration.getStringArray(FourDLanguage.FILE_SUFFIXES_KEY))
        .thenReturn(new String[] {"", " "});
    assertArrayEquals(new String[] {"4dm"}, language.getFileSuffixes());
  }

  @Test
  void returnedDefaultArrayCannotChangeLaterCalls() {
    language.getFileSuffixes()[0] = "changed";
    assertArrayEquals(new String[] {"4dm"}, language.getFileSuffixes());
  }
}
